from __future__ import annotations

from .models import (
    AnalysisContext,
    EvidenceObservation,
    EvidenceStatus,
    InvestigationObservation,
    InvestigationStepTrace,
    MetricKind,
)


class ObservationBuilder:
    def __init__(self, metric_kind: MetricKind):
        self.metric_kind = metric_kind

    def step(
        self,
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
            result_ids=list(result_ids),
            material_change=material_change,
            concentration_share=concentration_share,
            group_count=group_count,
            top_group=top_group,
            issue_codes=list(issue_codes),
        )

    def build(
        self,
        context: AnalysisContext,
        *,
        round_number: int,
        steps: list[InvestigationStepTrace],
        completed_dimensions: list[str],
        remaining_queries: int,
        remaining_planner_tokens: int | None,
        sensitive_dimensions: set[str] | None = None,
        candidate_dimensions: list[str] | None = None,
    ) -> InvestigationObservation:
        sensitive = sensitive_dimensions or set()
        evidence = [
            EvidenceObservation(
                dimension_id=step.dimension_id,
                status=step.status,
                result_ids=list(step.result_ids),
                material_change=step.material_change,
                concentration_share=step.concentration_share,
                group_count=step.group_count,
                top_group=(
                    None
                    if step.dimension_id in sensitive
                    else step.top_group
                ),
                issue_codes=list(step.issue_codes),
            )
            for step in steps
        ]
        return InvestigationObservation(
            analysis_id=context.analysis_id,
            round_number=round_number,
            metric_id=context.metric_id,
            metric_kind=self.metric_kind,
            question_kind=context.intent.question_kind,
            candidate_dimensions=list(
                context.intent.candidate_dimensions
                if candidate_dimensions is None
                else candidate_dimensions
            ),
            completed_dimensions=list(completed_dimensions),
            remaining_queries=remaining_queries,
            remaining_planner_tokens=remaining_planner_tokens,
            evidence=evidence,
        )
