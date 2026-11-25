from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb
import pytest

from metricledger.admission import AdmissionService
from metricledger.claims import ClaimRenderer, EvidenceValidator
from metricledger.compiler import QueryCompiler
from metricledger.data import DuckDbEngine
from metricledger.investigation import InvestigationService
from metricledger.models import (
    AnalysisIntent,
    BudgetPolicy,
    ClaimType,
    Completeness,
    DateRange,
    Principal,
    QuestionKind,
)
from metricledger.planners import HeuristicHypothesisPlanner
from metricledger.semantic import SemanticRegistry
from metricledger.snapshots import SnapshotRepository
from scripts.build_demo_data import DEMO_SNAPSHOTS, build_demo_database


TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 11, 23, 23, 59, 59, tzinfo=TZ)
CURRENT = DateRange(start=date(2026, 11, 17), end=date(2026, 11, 23))
PREVIOUS = DateRange(start=date(2026, 11, 10), end=date(2026, 11, 16))


@pytest.fixture
def registry() -> SemanticRegistry:
    return SemanticRegistry.load_default()


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "investigation.duckdb"
    build_demo_database(path)
    return path


@pytest.fixture
def principal(registry: SemanticRegistry) -> Principal:
    return Principal(
        principal_id="analyst",
        allowed_values={
            dimension.dimension_id: list(dimension.values)
            for dimension in registry.bundle.dimensions
        },
    )


def make_service(
    registry: SemanticRegistry,
    database,
    intent: AnalysisIntent,
    principal: Principal,
    budget: BudgetPolicy,
) -> InvestigationService:
    admission = AdmissionService(
        registry,
        SnapshotRepository.from_records(DEMO_SNAPSHOTS),
    )
    decision = admission.admit(
        intent,
        principal,
        as_of=AS_OF,
        budget=budget,
    )
    assert decision.context is not None
    snapshot = decision.snapshot
    assert snapshot is not None
    engine = DuckDbEngine(
        database,
        {snapshot.snapshot_id: snapshot.table_name},
    )
    return InvestigationService(
        registry=registry,
        compiler=QueryCompiler(registry),
        engine=engine,
        planner=HeuristicHypothesisPlanner(),
        context=decision.context,
    )


def additive_service(
    registry: SemanticRegistry,
    database,
    principal: Principal,
    budget: BudgetPolicy | None = None,
) -> InvestigationService:
    return make_service(
        registry,
        database,
        AnalysisIntent(
            metric_id="paid_gmv",
            question_kind=QuestionKind.INVESTIGATE,
            period=CURRENT,
            comparison_period=PREVIOUS,
        ),
        principal,
        budget or BudgetPolicy(max_queries=4),
    )


def test_additive_investigation_stops_after_concentrated_region(
    registry: SemanticRegistry,
    database,
    principal: Principal,
):
    service = additive_service(registry, database, principal)

    outcome = service.investigate()

    assert outcome.stop_reason == "concentrated_contribution"
    assert outcome.queries_used == 2
    assert outcome.top_dimension == "region"
    assert outcome.completeness is Completeness.COMPLETE

    contribution = next(
        claim
        for claim in outcome.claims
        if claim.claim_type is ClaimType.CONTRIBUTION
    )
    artifacts = {artifact.result_id: artifact for artifact in outcome.artifacts}
    validated = EvidenceValidator(registry).validate(contribution, artifacts)
    text = ClaimRenderer().render(validated)

    assert validated.evidence_values["contribution_share_pct"] == pytest.approx(
        100.0
    )
    assert "100.00%" in text
    assert "不代表因果" in text


def test_ratio_investigation_never_emits_arithmetic_contribution(
    registry: SemanticRegistry,
    database,
    principal: Principal,
):
    service = make_service(
        registry,
        database,
        AnalysisIntent(
            metric_id="paid_conversion_rate",
            question_kind=QuestionKind.INVESTIGATE,
            period=CURRENT,
            comparison_period=PREVIOUS,
            candidate_dimensions=["region"],
        ),
        principal,
        BudgetPolicy(max_queries=2),
    )

    outcome = service.investigate()

    assert outcome.queries_used == 2
    assert outcome.claims
    assert all(
        claim.claim_type is ClaimType.DIAGNOSTIC for claim in outcome.claims
    )
    artifacts = {artifact.result_id: artifact for artifact in outcome.artifacts}
    validated = EvidenceValidator(registry).validate(outcome.claims[0], artifacts)
    assert "numerator_value" in validated.evidence_values
    assert "denominator_value" in validated.evidence_values


def test_budget_is_reserved_before_query_execution(
    registry: SemanticRegistry,
    database,
    principal: Principal,
):
    service = additive_service(
        registry,
        database,
        principal,
        BudgetPolicy(max_queries=1),
    )

    outcome = service.investigate()

    assert outcome.queries_used == 1
    assert outcome.completeness is Completeness.PARTIAL
    assert "BUDGET_EXHAUSTED" in outcome.issue_codes


def test_contribution_share_uses_gross_group_movement_when_changes_offset(
    registry: SemanticRegistry,
    database,
    principal: Principal,
):
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            """
            UPDATE sales_batch_20261123_2300 SET paid_gmv = 100.0
            WHERE tenant_id = 'demo'
            """
        )
        connection.execute(
            """
            UPDATE sales_batch_20261123_2300
            SET paid_gmv = CASE region
                WHEN 'east' THEN 250.0
                WHEN 'west' THEN 0.0
                WHEN 'north' THEN 100.0
                WHEN 'south' THEN 200.0
            END
            WHERE tenant_id = 'demo'
              AND event_date BETWEEN DATE '2026-11-17' AND DATE '2026-11-23'
            """
        )
    service = make_service(
        registry,
        database,
        AnalysisIntent(
            metric_id="paid_gmv",
            question_kind=QuestionKind.INVESTIGATE,
            period=CURRENT,
            comparison_period=PREVIOUS,
            candidate_dimensions=["region"],
        ),
        principal,
        BudgetPolicy(max_queries=4),
    )

    outcome = service.investigate()

    contribution = next(
        claim
        for claim in outcome.claims
        if claim.claim_type is ClaimType.CONTRIBUTION
    )
    artifacts = {artifact.result_id: artifact for artifact in outcome.artifacts}
    validated = EvidenceValidator(registry).validate(contribution, artifacts)

    # Group deltas are +150, -100, 0, +100. The gross movement is 350,
    # so east accounts for 150/350, not 150/net-change 150.
    assert validated.evidence_values["contribution_share_pct"] == pytest.approx(
        300 / 7
    )
    assert "正负抵消" in ClaimRenderer().render(validated)
