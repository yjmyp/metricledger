from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from .models import (
    AdmissionDecision,
    AdmissionStatus,
    AnalysisContext,
    AnalysisIntent,
    BudgetPolicy,
    MetricDefinition,
    Principal,
    QualityCheck,
    QualityStatus,
    Scope,
    canonical_hash,
)
from .semantic import SemanticRegistry
from .snapshots import SnapshotRepository


class AdmissionService:
    def __init__(
        self,
        registry: SemanticRegistry,
        snapshots: SnapshotRepository,
    ):
        self.registry = registry
        self.snapshots = snapshots

    def admit(
        self,
        intent: AnalysisIntent,
        principal: Principal,
        *,
        as_of: datetime,
        budget: BudgetPolicy | None = None,
    ) -> AdmissionDecision:
        try:
            metric = self.registry.metric(intent.metric_id)
            self._validate_intent(intent, metric)
        except ValueError:
            return AdmissionDecision(
                status=AdmissionStatus.CLARIFY,
                issue_codes=["INVALID_INTENT"],
            )

        scope = self._effective_scope(principal)
        if not self._scope_is_sufficient(intent, principal, metric):
            return AdmissionDecision(
                status=AdmissionStatus.REFUSED,
                issue_codes=["SCOPE_NOT_AUTHORIZED"],
                effective_scope=scope,
            )

        snapshot = self.snapshots.select(as_of, intent.period)
        if snapshot is None:
            code = (
                "COMPARISON_NOT_COVERED"
                if intent.comparison_period is not None
                else "PERIOD_NOT_COVERED"
            )
            return AdmissionDecision(
                status=AdmissionStatus.UNAVAILABLE,
                issue_codes=[code],
                effective_scope=scope,
            )

        if (
            intent.comparison_period is not None
            and (
                snapshot.period_start > intent.comparison_period.start
                or snapshot.period_end < intent.comparison_period.end
            )
        ):
            return AdmissionDecision(
                status=AdmissionStatus.UNAVAILABLE,
                issue_codes=["COMPARISON_NOT_COVERED"],
                effective_scope=scope,
                snapshot=snapshot,
            )

        freshness = self.registry.freshness_policies[metric.freshness_policy_id]
        lag_hours = (as_of - snapshot.reporting_as_of).total_seconds() / 3600
        if lag_hours > freshness.max_lag_hours:
            return AdmissionDecision(
                status=AdmissionStatus.UNAVAILABLE,
                issue_codes=["SNAPSHOT_STALE"],
                effective_scope=scope,
                snapshot=snapshot,
            )

        quality = self._snapshot_quality(metric, snapshot)
        if any(check.status is QualityStatus.BLOCK for check in quality):
            return AdmissionDecision(
                status=AdmissionStatus.UNAVAILABLE,
                issue_codes=[
                    check.code
                    for check in quality
                    if check.status is QualityStatus.BLOCK
                ],
                effective_scope=scope,
                snapshot=snapshot,
                quality_checks=quality,
            )

        context = AnalysisContext(
            analysis_id=f"an_{uuid4().hex[:16]}",
            intent=intent,
            metric_id=metric.metric_id,
            semantic_bundle_id=self.registry.bundle.bundle_id,
            semantic_bundle_hash=self.registry.bundle.canonical_hash,
            snapshot_id=snapshot.snapshot_id,
            snapshot_table=snapshot.table_name,
            snapshot_manifest_hash=snapshot.manifest_hash,
            reporting_as_of=snapshot.reporting_as_of,
            timezone=self.registry.bundle.timezone,
            auth_policy_version=principal.auth_policy_version,
            effective_scope=scope,
            auth_scope_hash=canonical_hash(principal.model_dump(mode="json")),
            budget=budget or BudgetPolicy(),
        )
        return AdmissionDecision(
            status=AdmissionStatus.ADMITTED,
            effective_scope=scope,
            snapshot=snapshot,
            quality_checks=quality,
            context=context,
        )

    def _validate_intent(
        self, intent: AnalysisIntent, metric: MetricDefinition
    ) -> None:
        if metric.metric_id != intent.metric_id:
            raise ValueError("intent metric does not match semantic metric")
        if metric.kind.value == "RATIO" and intent.comparison_period is None:
            if intent.question_kind.value != "VALUE":
                raise ValueError("ratio change requires comparison period")
        for dimension_id, values in intent.filters.items():
            if dimension_id not in metric.allowed_dimensions:
                raise ValueError(f"metric does not allow dimension {dimension_id}")
            self.registry.validate_filter(dimension_id, values)
        for dimension_id in intent.candidate_dimensions:
            if dimension_id not in metric.allowed_dimensions:
                raise ValueError(f"metric does not allow dimension {dimension_id}")

    def _effective_scope(self, principal: Principal) -> Scope:
        values: dict[str, list[str]] = {}
        for dimension_id, dimension in self.registry.dimensions.items():
            allowed = principal.allowed_values.get(dimension_id, [])
            values[dimension_id] = [
                value for value in dimension.values if value in set(allowed)
            ]
        return Scope(values=values)

    def _scope_is_sufficient(
        self,
        intent: AnalysisIntent,
        principal: Principal,
        metric: MetricDefinition,
    ) -> bool:
        for dimension_id in metric.allowed_dimensions:
            dimension = self.registry.dimension(dimension_id)
            allowed = set(principal.allowed_values.get(dimension_id, []))
            requested = intent.filters.get(dimension_id, dimension.values)
            if not set(requested).issubset(allowed):
                return False
        return True

    def _snapshot_quality(
        self,
        metric: MetricDefinition,
        snapshot,
    ) -> list[QualityCheck]:
        policy = self.registry.quality_policies[metric.quality_policy_id]
        checks = [
            QualityCheck(
                code="SNAPSHOT_COMPLETE",
                status=QualityStatus.PASS if snapshot.complete else QualityStatus.BLOCK,
                message="快照批次完整" if snapshot.complete else "快照批次不完整",
            ),
            QualityCheck(
                code="DUPLICATE_RATE",
                status=(
                    QualityStatus.PASS
                    if snapshot.duplicate_rate <= policy.max_duplicate_rate
                    else QualityStatus.BLOCK
                ),
                message="业务主键重复率检查",
                value=snapshot.duplicate_rate,
            ),
            QualityCheck(
                code="NULL_RATE",
                status=(
                    QualityStatus.PASS
                    if snapshot.null_rate <= policy.max_null_rate
                    else QualityStatus.BLOCK
                ),
                message="关键字段非空率检查",
                value=snapshot.null_rate,
            ),
        ]
        return checks

