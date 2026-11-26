from __future__ import annotations

from uuid import uuid4

from .claims import EvidenceValidator
from .compiler import QueryCompiler
from .data import DuckDbEngine, QueryExecutionError
from .evidence import result_digest
from .models import (
    AnalysisContext,
    Claim,
    ClaimScope,
    ClaimType,
    Completeness,
    DateRange,
    InvestigationOutcome,
    MetricKind,
    ObservationSummary,
    ResultArtifact,
    SnapshotRecord,
)
from .planners import HypothesisPlanner
from .semantic import SemanticRegistry


class InvestigationService:
    def __init__(
        self,
        *,
        registry: SemanticRegistry,
        compiler: QueryCompiler,
        engine: DuckDbEngine,
        planner: HypothesisPlanner,
        context: AnalysisContext,
        initial_artifacts: list[ResultArtifact] | None = None,
        initial_queries_used: int = 0,
    ):
        self.registry = registry
        self.compiler = compiler
        self.engine = engine
        self.planner = planner
        self.context = context
        self._queries_used = initial_queries_used
        self._artifacts: list[ResultArtifact] = list(initial_artifacts or [])
        self._issue_codes: list[str] = []

    def investigate(self) -> InvestigationOutcome:
        metric = self.registry.metric(self.context.metric_id)
        allowed_dimensions = [
            self.registry.dimension(dimension_id)
            for dimension_id in metric.allowed_dimensions
            if not self.context.intent.candidate_dimensions
            or dimension_id in self.context.intent.candidate_dimensions
        ]
        observation = ObservationSummary(
            analysis_id=self.context.analysis_id,
            metric_id=metric.metric_id,
            metric_kind=metric.kind,
            question_kind=self.context.intent.question_kind,
            candidate_dimensions=[
                dimension.dimension_id for dimension in allowed_dimensions
            ],
            evidence_summaries=[],
        )
        planner_decision = self.planner.propose(
            observation,
            allowed_dimensions,
            remaining_queries=max(
                0, self.context.budget.max_queries - self._queries_used
            ),
            round_number=1,
        )
        hypotheses = planner_decision.hypotheses

        claims: list[Claim] = []
        top_dimension: str | None = None
        stop_reason = "budget_exhausted"
        completeness = Completeness.NONE

        for hypothesis in hypotheses:
            if self._remaining_queries() <= 0:
                break

            current = self._execute_grouped(
                hypothesis.dimension_id,
                self.context.intent.period,
                include_ratio_components=metric.kind is MetricKind.RATIO,
            )
            if current is None:
                break
            if self.context.intent.comparison_period is None:
                self._issue_codes.append("COMPARISON_PERIOD_REQUIRED")
                stop_reason = "comparison_unavailable"
                completeness = Completeness.PARTIAL
                break

            baseline = self._execute_grouped(
                hypothesis.dimension_id,
                self.context.intent.comparison_period,
                include_ratio_components=metric.kind is MetricKind.RATIO,
            )
            if baseline is None:
                stop_reason = "budget_exhausted"
                completeness = Completeness.PARTIAL
                break

            if metric.kind is MetricKind.ADDITIVE:
                contribution = self._additive_contribution(
                    dimension_id=hypothesis.dimension_id,
                    current=current,
                    baseline=baseline,
                )
                if contribution is None:
                    stop_reason = "no_material_change"
                    completeness = Completeness.PARTIAL
                    break
                claim, concentration = contribution
                claims.append(claim)
                top_dimension = hypothesis.dimension_id
                if concentration >= 0.5:
                    stop_reason = "concentrated_contribution"
                    completeness = Completeness.COMPLETE
                    break
            else:
                claims.extend(
                    self._ratio_diagnostics(
                        dimension_id=hypothesis.dimension_id,
                        current=current,
                        baseline=baseline,
                    )
                )
                top_dimension = hypothesis.dimension_id
                stop_reason = "ratio_diagnostic_complete"
                completeness = Completeness.COMPLETE
                break
        else:
            if self._queries_used > 0:
                completeness = Completeness.PARTIAL
                self._issue_codes.append("BUDGET_EXHAUSTED")

        if self._remaining_queries() <= 0 and completeness is not Completeness.COMPLETE:
            stop_reason = "budget_exhausted"
            completeness = Completeness.PARTIAL
            if "BUDGET_EXHAUSTED" not in self._issue_codes:
                self._issue_codes.append("BUDGET_EXHAUSTED")

        return InvestigationOutcome(
            analysis_id=self.context.analysis_id,
            completeness=completeness,
            stop_reason=stop_reason,
            queries_used=self._queries_used,
            top_dimension=top_dimension,
            claims=claims,
            artifacts=self._artifacts,
            issue_codes=self._issue_codes,
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
            self._issue_codes.append("DIAGNOSTIC_QUERY_FAILED")
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
