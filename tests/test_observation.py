from datetime import date, datetime
from zoneinfo import ZoneInfo

from metricledger.models import (
    AnalysisContext,
    AnalysisIntent,
    BudgetPolicy,
    DateRange,
    EvidenceStatus,
    MetricKind,
    QuestionKind,
    ResultArtifact,
    Scope,
)
from metricledger.observation import ObservationBuilder


def context(candidate_dimensions, budget=BudgetPolicy(max_queries=6)):
    return AnalysisContext(
        analysis_id="an_observation",
        tenant_id="demo",
        intent=AnalysisIntent(
            metric_id="paid_gmv",
            question_kind=QuestionKind.INVESTIGATE,
            period=DateRange(
                start=date(2026, 11, 17), end=date(2026, 11, 23)
            ),
            comparison_period=DateRange(
                start=date(2026, 11, 10), end=date(2026, 11, 16)
            ),
            candidate_dimensions=candidate_dimensions,
        ),
        metric_id="paid_gmv",
        semantic_bundle_id="ecommerce",
        semantic_bundle_hash="semantic-hash",
        snapshot_id="snapshot",
        snapshot_table="sales",
        snapshot_manifest_hash="snapshot-hash",
        reporting_as_of=datetime(
            2026, 11, 23, 23, 59, tzinfo=ZoneInfo("Asia/Shanghai")
        ),
        timezone="Asia/Shanghai",
        auth_policy_version="v1",
        effective_scope=Scope(values={"region": ["east"]}),
        auth_scope_hash="auth-hash",
        budget=budget,
    )


def test_observation_builder_emits_bounded_evidence_without_raw_rows():
    builder = ObservationBuilder(MetricKind.ADDITIVE)
    ctx = context(["region"])
    artifact = ResultArtifact(
        result_id="r-region",
        analysis_id=ctx.analysis_id,
        analysis_kind="diagnostic_group",
        intent_dsl=ctx.intent,
        compiled_sql="SELECT secret",
        compiled_parameters=[],
        sql_fingerprint="fp",
        row_count=1,
        result_digest="digest",
        bounded_rows=[{"dimension_value": "east", "metric_value": 10.0}],
        metric_versions={"paid_gmv": "1"},
        snapshot_id="snapshot",
        semantic_bundle_hash="semantic-hash",
        scope=ctx.effective_scope,
    )
    step = builder.step(
        round_number=1,
        dimension_id="region",
        status=EvidenceStatus.MATERIAL_CHANGE,
        result_ids=[artifact.result_id],
        material_change=10.0,
        concentration_share=0.75,
        group_count=1,
        top_group="east",
        issue_codes=[],
    )

    observation = builder.build(
        ctx,
        round_number=1,
        steps=[step],
        completed_dimensions=["region"],
        remaining_queries=4,
        remaining_planner_tokens=1000,
    )
    payload = observation.model_dump(mode="json")

    assert payload["evidence"][0]["top_group"] == "east"
    assert "bounded_rows" not in str(payload)
    assert "SELECT secret" not in str(payload)


def test_sensitive_dimension_omits_top_group():
    builder = ObservationBuilder(MetricKind.ADDITIVE)
    ctx = context(["region"])
    step = builder.step(
        round_number=1,
        dimension_id="region",
        status=EvidenceStatus.MATERIAL_CHANGE,
        result_ids=["r1"],
        material_change=5.0,
        concentration_share=0.4,
        group_count=2,
        top_group="east",
        issue_codes=[],
    )

    observation = builder.build(
        ctx,
        round_number=1,
        steps=[step],
        completed_dimensions=["region"],
        remaining_queries=2,
        remaining_planner_tokens=500,
        sensitive_dimensions={"region"},
    )

    assert observation.evidence[0].top_group is None
