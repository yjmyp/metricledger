from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from .claims import EvidenceValidator
from .compiler import QueryCompiler
from .context_guard import ContextIntegrityError, ContextIntegrityGuard
from .data import DuckDbEngine, QueryExecutionError
from .evidence import result_digest
from .models import (
    AnalysisContext,
    Claim,
    ClaimScope,
    ClaimType,
    Completeness,
    DateRange,
    EvidenceStatus,
    InvestigationRoundTrace,
    InvestigationOutcome,
    InvestigationStepTrace,
    InvestigationTrace,
    MetricKind,
    PlannerAttemptTrace,
    ResultArtifact,
    SnapshotRecord,
)
from .observation import ObservationBuilder
from .planners import HeuristicHypothesisPlanner, HypothesisPlanner
from .semantic import SemanticRegistry
from .snapshots import SnapshotRepository


@dataclass(frozen=True)
class _Diagnosis:
    step: InvestigationStepTrace
    claims: list[Claim]
    top_dimension: str | None
    stop_reason: str | None


class InvestigationService:
    def __init__(
        self,
        *,
        registry: SemanticRegistry,
        compiler: QueryCompiler,
        engine: DuckDbEngine,
        planner: HypothesisPlanner,
        context: AnalysisContext,
        guard: ContextIntegrityGuard | None = None,
        snapshots: SnapshotRepository | None = None,
        fallback_planner: HypothesisPlanner | None = None,
        initial_artifacts: list[ResultArtifact] | None = None,
        initial_queries_used: int = 0,
    ):
        self.registry = registry
        self.compiler = compiler
        self.engine = engine
        self.planner = planner
        self.fallback_planner = fallback_planner or HeuristicHypothesisPlanner()
        self.context = context
        self.guard = guard
        self.snapshots = snapshots
        self._queries_used = initial_queries_used
        self._artifacts: list[ResultArtifact] = list(initial_artifacts or [])
        self._issue_codes: list[str] = []

    def investigate(self) -> InvestigationOutcome:
        metric = self.registry.metric(self.context.metric_id)
        candidates = self._candidate_dimensions(metric)
        sensitive_dimensions = {
            dimension.dimension_id
            for dimension in candidates
            if dimension.sensitive
        }
        completed: list[str] = []
        all_steps: list[InvestigationStepTrace] = []
        rounds: list[InvestigationRoundTrace] = []
        claims: list[Claim] = []
        top_dimension: str | None = None
        planner_tokens_used: int | None = 0
        planner_usage_complete = True
        stop_reason = "no_remaining_candidates"
        completeness = Completeness.NONE

        for round_number in range(
            1, self.context.budget.max_planner_rounds + 1
        ):
            remaining = [
                dimension
                for dimension in candidates
                if dimension.dimension_id not in completed
            ]
            if not remaining:
                stop_reason = "all_candidates_observed"
                break
            if self._remaining_queries() < 2:
                self._add_issue("BUDGET_EXHAUSTED")
                stop_reason = "budget_exhausted"
                rounds.append(
                    self._not_run_round(
                        metric=metric,
                        round_number=round_number,
                        all_steps=all_steps,
                        completed=completed,
                        sensitive_dimensions=sensitive_dimensions,
                        planner_usage_complete=planner_usage_complete,
                        planner_tokens_used=planner_tokens_used,
                        reason="fewer than two remaining queries",
                    )
                )
                break
            if (
                planner_usage_complete
                and planner_tokens_used is not None
                and planner_tokens_used
                >= self.context.budget.max_planner_tokens
            ):
                self._add_issue("PLANNER_TOKEN_BUDGET_EXHAUSTED")
                stop_reason = "planner_token_budget_exhausted"
                rounds.append(
                    self._not_run_round(
                        metric=metric,
                        round_number=round_number,
                        all_steps=all_steps,
                        completed=completed,
                        sensitive_dimensions=sensitive_dimensions,
                        planner_usage_complete=planner_usage_complete,
                        planner_tokens_used=planner_tokens_used,
                        reason="planner token budget exhausted",
                    )
                )
                break
            if self._context_drifted():
                stop_reason = "context_drift"
                break

            observation = self._build_observation(
                metric=metric,
                round_number=round_number,
                all_steps=all_steps,
                completed=completed,
                sensitive_dimensions=sensitive_dimensions,
                planner_usage_complete=planner_usage_complete,
                planner_tokens_used=planner_tokens_used,
            )
            decision = self._propose(
                observation=observation,
                remaining=remaining,
                round_number=round_number,
                planner_usage_complete=planner_usage_complete,
            )
            round_steps: list[InvestigationStepTrace] = []
            round_complete = False
            for hypothesis in decision.hypotheses[:3]:
                if hypothesis.dimension_id not in {
                    dimension.dimension_id for dimension in remaining
                }:
                    continue
                if hypothesis.dimension_id in completed:
                    continue
                if self._remaining_queries() < 2:
                    self._add_issue("BUDGET_EXHAUSTED")
                    stop_reason = "budget_exhausted"
                    break

                result = self._diagnose(
                    dimension_id=hypothesis.dimension_id,
                    round_number=round_number,
                )
                round_steps.append(result.step)
                all_steps.append(result.step)
                if result.stop_reason != "context_drift":
                    completed.append(hypothesis.dimension_id)
                claims.extend(result.claims)
                if result.top_dimension is not None:
                    top_dimension = result.top_dimension
                if result.stop_reason is not None:
                    stop_reason = result.stop_reason
                    round_complete = True
                    break

            if decision.usage is not None and planner_usage_complete:
                planner_tokens_used = (
                    (planner_tokens_used or 0) + decision.usage.total_tokens
                )
            elif (
                decision.usage is None
                or not decision.attempt.usage_complete
            ):
                planner_usage_complete = False
                planner_tokens_used = None

            rounds.append(
                InvestigationRoundTrace(
                    round_number=round_number,
                    observation=observation,
                    planner=decision.attempt,
                    steps=round_steps,
                )
            )
            if round_complete or stop_reason == "context_drift":
                break

        remaining_dimensions = [
            dimension.dimension_id
            for dimension in candidates
            if dimension.dimension_id not in completed
        ]
        if stop_reason in {
            "concentrated_contribution",
            "ratio_diagnostic_complete",
        }:
            completeness = Completeness.COMPLETE
        elif stop_reason == "context_drift":
            claims = []
            completeness = Completeness.NONE
        elif completed:
            completeness = Completeness.PARTIAL

        if (
            stop_reason == "no_remaining_candidates"
            and len(rounds) == self.context.budget.max_planner_rounds
        ):
            stop_reason = "planner_round_limit"

        trace = InvestigationTrace(
            rounds=rounds,
            completed_dimensions=completed,
            remaining_dimensions=remaining_dimensions,
            stop_reason=stop_reason,
            planner_fallback_used=(
                not planner_usage_complete
                or any(
                    round_trace.planner.fallback_used
                    for round_trace in rounds
                )
            ),
        )

        return InvestigationOutcome(
            analysis_id=self.context.analysis_id,
            completeness=completeness,
            stop_reason=stop_reason,
            queries_used=self._queries_used,
            top_dimension=top_dimension,
            claims=claims,
            artifacts=self._artifacts,
            issue_codes=self._issue_codes,
            planner_tokens_used=planner_tokens_used,
            planner_usage_complete=planner_usage_complete,
            investigation=trace,
        )

    def _diagnose(
        self,
        *,
        dimension_id: str,
        round_number: int,
    ) -> _Diagnosis:
        metric = self.registry.metric(self.context.metric_id)
        if self._context_drifted():
            return self._drift_diagnosis(
                round_number=round_number,
                dimension_id=dimension_id,
            )

        current = self._execute_grouped(
            dimension_id,
            self.context.intent.period,
            include_ratio_components=metric.kind is MetricKind.RATIO,
        )
        if current is None:
            self._add_issue("DIAGNOSTIC_QUERY_FAILED")
            return _Diagnosis(
                step=self._step(
                    round_number=round_number,
                    dimension_id=dimension_id,
                    status=EvidenceStatus.QUERY_FAILED,
                    result_ids=[],
                    material_change=None,
                    concentration_share=None,
                    group_count=0,
                    top_group=None,
                    issue_codes=["DIAGNOSTIC_QUERY_FAILED"],
                ),
                claims=[],
                top_dimension=None,
                stop_reason=None,
            )

        if self.context.intent.comparison_period is None:
            self._add_issue("COMPARISON_PERIOD_REQUIRED")
            return _Diagnosis(
                step=self._step(
                    round_number=round_number,
                    dimension_id=dimension_id,
                    status=EvidenceStatus.NO_COMPARABLE_GROUPS,
                    result_ids=[current.result_id],
                    material_change=None,
                    concentration_share=None,
                    group_count=current.row_count,
                    top_group=None,
                    issue_codes=["COMPARISON_PERIOD_REQUIRED"],
                ),
                claims=[],
                top_dimension=None,
                stop_reason="comparison_unavailable",
            )

        if self._context_drifted():
            return self._drift_diagnosis(
                round_number=round_number,
                dimension_id=dimension_id,
                result_ids=[current.result_id],
            )

        baseline = self._execute_grouped(
            dimension_id,
            self.context.intent.comparison_period,
            include_ratio_components=metric.kind is MetricKind.RATIO,
        )
        if baseline is None:
            self._add_issue("DIAGNOSTIC_QUERY_FAILED")
            return _Diagnosis(
                step=self._step(
                    round_number=round_number,
                    dimension_id=dimension_id,
                    status=EvidenceStatus.QUERY_FAILED,
                    result_ids=[current.result_id],
                    material_change=None,
                    concentration_share=None,
                    group_count=current.row_count,
                    top_group=None,
                    issue_codes=["DIAGNOSTIC_QUERY_FAILED"],
                ),
                claims=[],
                top_dimension=None,
                stop_reason=None,
            )

        if metric.kind is MetricKind.RATIO:
            current_groups = self._group_values(current)
            baseline_groups = self._group_values(baseline)
            common_groups = sorted(
                set(current_groups) & set(baseline_groups)
            )
            if not common_groups:
                return _Diagnosis(
                    step=self._step(
                        round_number=round_number,
                        dimension_id=dimension_id,
                        status=EvidenceStatus.NO_COMPARABLE_GROUPS,
                        result_ids=[
                            baseline.result_id,
                            current.result_id,
                        ],
                        material_change=None,
                        concentration_share=None,
                        group_count=0,
                        top_group=None,
                        issue_codes=[],
                    ),
                    claims=[],
                    top_dimension=None,
                    stop_reason=None,
                )
            top_group = max(
                common_groups,
                key=lambda group: abs(
                    current_groups[group] - baseline_groups[group]
                ),
            )
            claims = self._ratio_diagnostics(
                dimension_id=dimension_id,
                current=current,
                baseline=baseline,
            )
            return _Diagnosis(
                step=self._step(
                    round_number=round_number,
                    dimension_id=dimension_id,
                    status=EvidenceStatus.MATERIAL_CHANGE,
                    result_ids=[baseline.result_id, current.result_id],
                    material_change=abs(
                        current_groups[top_group] - baseline_groups[top_group]
                    ),
                    concentration_share=None,
                    group_count=len(common_groups),
                    top_group=top_group,
                    issue_codes=[],
                ),
                claims=claims,
                top_dimension=dimension_id,
                stop_reason="ratio_diagnostic_complete",
            )

        current_groups = self._group_values(current)
        baseline_groups = self._group_values(baseline)
        all_groups = sorted(set(current_groups) | set(baseline_groups))
        deltas = {
            group: current_groups.get(group, 0.0)
            - baseline_groups.get(group, 0.0)
            for group in all_groups
        }
        total_absolute_delta = sum(abs(value) for value in deltas.values())
        contribution = self._additive_contribution(
            dimension_id=dimension_id,
            current=current,
            baseline=baseline,
        )
        if contribution is None:
            return _Diagnosis(
                step=self._step(
                    round_number=round_number,
                    dimension_id=dimension_id,
                    status=EvidenceStatus.NO_MATERIAL_CHANGE,
                    result_ids=[baseline.result_id, current.result_id],
                    material_change=total_absolute_delta,
                    concentration_share=0.0,
                    group_count=len(all_groups),
                    top_group=None,
                    issue_codes=[],
                ),
                claims=[],
                top_dimension=None,
                stop_reason=None,
            )
        claim, concentration = contribution
        stop_reason = (
            "concentrated_contribution" if concentration >= 0.5 else None
        )
        return _Diagnosis(
            step=self._step(
                round_number=round_number,
                dimension_id=dimension_id,
                status=EvidenceStatus.MATERIAL_CHANGE,
                result_ids=[baseline.result_id, current.result_id],
                material_change=total_absolute_delta,
                concentration_share=concentration,
                group_count=len(all_groups),
                top_group=claim.scope.group_value,
                issue_codes=[],
            ),
            claims=[claim],
            top_dimension=dimension_id,
            stop_reason=stop_reason,
        )

    def _execute_grouped(
        self,
        dimension_id: str,
        period: DateRange,
        *,
        include_ratio_components: bool,
    ) -> ResultArtifact | None:
        if self._remaining_queries() <= 0:
            return None

        self._queries_used += 1
        metric = self.registry.metric(self.context.metric_id)
        intent = self.context.intent.model_copy(
            update={
                "period": period,
                "candidate_dimensions": [dimension_id],
            }
        )
        try:
            compiled = self.compiler.compile(
                intent,
                metric,
                self._snapshot(),
                self.context.effective_scope,
                group_by=dimension_id,
                include_ratio_components=include_ratio_components,
            )
            rows = self.engine.execute(
                compiled,
                self.context.snapshot_id,
            )
        except (QueryExecutionError, ValueError):
            return None

        artifact = ResultArtifact(
            result_id=f"result_{uuid4().hex[:16]}",
            analysis_id=self.context.analysis_id,
            analysis_kind="diagnostic_group",
            intent_dsl=intent,
            compiled_sql=compiled.sql,
            compiled_parameters=compiled.parameters,
            sql_fingerprint=compiled.fingerprint,
            row_count=rows.row_count,
            result_digest="",
            bounded_rows=rows.rows,
            metric_versions={metric.metric_id: self.registry.bundle.version},
            snapshot_id=self.context.snapshot_id,
            semantic_bundle_hash=self.context.semantic_bundle_hash,
            scope=self.context.effective_scope,
            elapsed_ms=rows.elapsed_ms,
        )
        sealed = artifact.model_copy(
            update={"result_digest": result_digest(artifact)}
        )
        self._artifacts.append(sealed)
        return sealed

    def _additive_contribution(
        self,
        *,
        dimension_id: str,
        current: ResultArtifact,
        baseline: ResultArtifact,
    ) -> tuple[Claim, float] | None:
        current_groups = self._group_values(current)
        baseline_groups = self._group_values(baseline)
        all_groups = sorted(set(current_groups) | set(baseline_groups))
        deltas = {
            group: current_groups.get(group, 0.0)
            - baseline_groups.get(group, 0.0)
            for group in all_groups
        }
        total_absolute_delta = sum(abs(value) for value in deltas.values())
        if total_absolute_delta == 0:
            return None
        top_group = max(all_groups, key=lambda group: abs(deltas[group]))
        concentration = abs(deltas[top_group]) / total_absolute_delta
        has_offsetting_changes = (
            any(value > 0 for value in deltas.values())
            and any(value < 0 for value in deltas.values())
        )
        offsetting_note = (
            "；各分组存在正负抵消，该占比不等于对净变化的解释力。"
            if has_offsetting_changes
            else "。"
        )
        claim = Claim(
            claim_id=f"claim_{uuid4().hex[:16]}",
            analysis_id=self.context.analysis_id,
            claim_type=ClaimType.CONTRIBUTION,
            template=(
                f"{top_group} 的{dimension_id}分组对全部分组绝对变化的占比为 "
                "{{evidence:contribution_share_pct}}"
                f"{offsetting_note}"
                "这只表示可加指标的分组差异，不代表因果。"
            ),
            evidence_refs=[baseline.result_id, current.result_id],
            scope=ClaimScope(
                metric_id=self.context.metric_id,
                period=self.context.intent.period,
                comparison_period=self.context.intent.comparison_period,
                filters=self.context.effective_scope.values,
                group_by=dimension_id,
                group_value=top_group,
            ),
            limitations=["分组算术贡献不代表因果关系"],
        )
        EvidenceValidator(self.registry).validate(
            claim,
            {artifact.result_id: artifact for artifact in self._artifacts},
        )
        return claim, concentration

    def _ratio_diagnostics(
        self,
        *,
        dimension_id: str,
        current: ResultArtifact,
        baseline: ResultArtifact,
    ) -> list[Claim]:
        current_groups = self._group_values(current)
        baseline_groups = self._group_values(baseline)
        common_groups = sorted(set(current_groups) & set(baseline_groups))
        if not common_groups:
            return []
        top_group = max(
            common_groups,
            key=lambda group: abs(
                current_groups[group] - baseline_groups[group]
            ),
        )
        metric = self.registry.metric(self.context.metric_id)
        claims = [
            Claim(
                claim_id=f"claim_{uuid4().hex[:16]}",
                analysis_id=self.context.analysis_id,
                claim_type=ClaimType.DIAGNOSTIC,
                template=(
                    f"{top_group} 的{dimension_id}分组当前"
                    + f"{metric.display_name}为 "
                    + "{{evidence:metric_value}}，"
                    + "分子为 {{evidence:numerator_value}}，"
                    + "分母为 {{evidence:denominator_value}}。"
                ),
                evidence_refs=[current.result_id],
                scope=ClaimScope(
                    metric_id=self.context.metric_id,
                    period=self.context.intent.period,
                    filters=self.context.effective_scope.values,
                    group_by=dimension_id,
                    group_value=top_group,
                ),
            ),
            Claim(
                claim_id=f"claim_{uuid4().hex[:16]}",
                analysis_id=self.context.analysis_id,
                claim_type=ClaimType.DIAGNOSTIC,
                template=(
                    f"{top_group} 的{dimension_id}分组上一周期"
                    + f"{metric.display_name}为 "
                    + "{{evidence:metric_value}}，"
                    + "分子为 {{evidence:numerator_value}}，"
                    + "分母为 {{evidence:denominator_value}}。"
                ),
                evidence_refs=[baseline.result_id],
                scope=ClaimScope(
                    metric_id=self.context.metric_id,
                    period=baseline.intent_dsl.period,
                    filters=self.context.effective_scope.values,
                    group_by=dimension_id,
                    group_value=top_group,
                ),
            ),
        ]
        artifact_map = {
            artifact.result_id: artifact for artifact in self._artifacts
        }
        validator = EvidenceValidator(self.registry)
        for claim in claims:
            validator.validate(claim, artifact_map)
        return claims

    @staticmethod
    def _group_values(artifact: ResultArtifact) -> dict[str, float]:
        values: dict[str, float] = {}
        for row in artifact.bounded_rows:
            dimension_value = row.get("dimension_value")
            metric_value = row.get("metric_value")
            if isinstance(dimension_value, str) and isinstance(
                metric_value, (int, float)
            ):
                values[dimension_value] = float(metric_value)
        return values

    def _snapshot(self):
        comparison = self.context.intent.comparison_period
        return SnapshotRecord(
            snapshot_id=self.context.snapshot_id,
            table_name=self.context.snapshot_table,
            reporting_as_of=self.context.reporting_as_of,
            period_start=min(
                self.context.intent.period.start,
                comparison.start if comparison else self.context.intent.period.start,
            ),
            period_end=max(
                self.context.intent.period.end,
                comparison.end if comparison else self.context.intent.period.end,
            ),
            manifest_hash=self.context.snapshot_manifest_hash,
        )

    def _remaining_queries(self) -> int:
        return max(0, self.context.budget.max_queries - self._queries_used)

    def _candidate_dimensions(self, metric) -> list:
        return [
            self.registry.dimension(dimension_id)
            for dimension_id in metric.allowed_dimensions
            if not self.context.intent.candidate_dimensions
            or dimension_id in self.context.intent.candidate_dimensions
        ]

    def _build_observation(
        self,
        *,
        metric,
        round_number: int,
        all_steps: list[InvestigationStepTrace],
        completed: list[str],
        sensitive_dimensions: set[str],
        planner_usage_complete: bool,
        planner_tokens_used: int | None,
    ):
        remaining_planner_tokens = (
            max(
                0,
                self.context.budget.max_planner_tokens
                - (planner_tokens_used or 0),
            )
            if planner_usage_complete and planner_tokens_used is not None
            else None
        )
        return ObservationBuilder(metric.kind).build(
            self.context,
            round_number=round_number,
            steps=all_steps,
            completed_dimensions=completed,
            remaining_queries=self._remaining_queries(),
            remaining_planner_tokens=remaining_planner_tokens,
            sensitive_dimensions=sensitive_dimensions,
            candidate_dimensions=[
                dimension.dimension_id
                for dimension in self._candidate_dimensions(metric)
            ],
        )

    def _not_run_round(
        self,
        *,
        metric,
        round_number: int,
        all_steps: list[InvestigationStepTrace],
        completed: list[str],
        sensitive_dimensions: set[str],
        planner_usage_complete: bool,
        planner_tokens_used: int | None,
        reason: str,
    ) -> InvestigationRoundTrace:
        observation = self._build_observation(
            metric=metric,
            round_number=round_number,
            all_steps=all_steps,
            completed=completed,
            sensitive_dimensions=sensitive_dimensions,
            planner_usage_complete=planner_usage_complete,
            planner_tokens_used=planner_tokens_used,
        )
        return InvestigationRoundTrace(
            round_number=round_number,
            observation=observation,
            planner=PlannerAttemptTrace(
                round_number=round_number,
                outcome="NOT_RUN",
                model_called=False,
                fallback_reason=reason,
                usage_complete=planner_usage_complete,
                selected_dimensions=[],
            ),
            steps=[],
        )

    def _propose(
        self,
        *,
        observation,
        remaining: list,
        round_number: int,
        planner_usage_complete: bool,
    ):
        planner = self.planner if planner_usage_complete else self.fallback_planner
        return planner.propose(
            observation,
            remaining,
            remaining_queries=self._remaining_queries(),
            round_number=round_number,
        )

    def _context_drifted(self) -> bool:
        if self.guard is None:
            return False
        try:
            self.guard.verify(self.context)
        except ContextIntegrityError:
            self._add_issue("CONTEXT_DRIFT")
            return True
        return False

    def _drift_diagnosis(
        self,
        *,
        round_number: int,
        dimension_id: str,
        result_ids: list[str] | None = None,
    ) -> _Diagnosis:
        self._add_issue("CONTEXT_DRIFT")
        return _Diagnosis(
            step=self._step(
                round_number=round_number,
                dimension_id=dimension_id,
                status=EvidenceStatus.NO_MATERIAL_CHANGE,
                result_ids=list(result_ids or []),
                material_change=None,
                concentration_share=None,
                group_count=0,
                top_group=None,
                issue_codes=["CONTEXT_DRIFT"],
            ),
            claims=[],
            top_dimension=None,
            stop_reason="context_drift",
        )

    @staticmethod
    def _step(
        *,
        round_number: int,
        dimension_id: str,
        status: EvidenceStatus,
        result_ids: list[str],
        material_change: float | None,
        concentration_share: float | None,
        group_count: int,
        top_group: str | None,
        issue_codes: list[str],
    ) -> InvestigationStepTrace:
        return InvestigationStepTrace(
            round_number=round_number,
            dimension_id=dimension_id,
            status=status,
            result_ids=result_ids,
            material_change=material_change,
            concentration_share=concentration_share,
            group_count=group_count,
            top_group=top_group,
            issue_codes=issue_codes,
        )

    def _add_issue(self, code: str) -> None:
        if code not in self._issue_codes:
            self._issue_codes.append(code)
