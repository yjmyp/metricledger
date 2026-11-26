from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb
import pytest

from metricledger.admission import AdmissionService
from metricledger.claims import ClaimRenderer, EvidenceValidator
from metricledger.compiler import QueryCompiler
from metricledger.context_guard import ContextIntegrityGuard
from metricledger.data import DuckDbEngine, QueryExecutionError
from metricledger.investigation import InvestigationService
from metricledger.models import (
    AnalysisIntent,
    BudgetPolicy,
    ClaimType,
    Completeness,
    DateRange,
    EvidenceStatus,
    Hypothesis,
    ModelUsage,
    PlannerAttemptTrace,
    PlannerDecision,
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
    *,
    planner=None,
    initial_queries_used: int = 0,
) -> InvestigationService:
    snapshots = SnapshotRepository.from_records(DEMO_SNAPSHOTS)
    admission = AdmissionService(
        registry,
        snapshots,
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
        planner=planner or HeuristicHypothesisPlanner(),
        context=decision.context,
        guard=ContextIntegrityGuard(registry, snapshots),
        snapshots=snapshots,
        initial_queries_used=initial_queries_used,
    )


def additive_service(
    registry: SemanticRegistry,
    database,
    principal: Principal,
    budget: BudgetPolicy | None = None,
    *,
    initial_queries_used: int = 0,
) -> InvestigationService:
    return make_service(
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
        budget or BudgetPolicy(max_queries=4),
        initial_queries_used=initial_queries_used,
    )


class RecordingPlanner:
    def __init__(self):
        self.calls = []

    def propose(
        self,
        observation,
        allowed_dimensions,
        *,
        remaining_queries,
        round_number,
    ):
        self.calls.append(observation)
        dimension = "channel" if round_number == 1 else "category"
        return PlannerDecision(
            hypotheses=[
                Hypothesis(
                    dimension_id=dimension,
                    rationale_code="follow_up",
                    priority=0,
                )
            ],
            attempt=PlannerAttemptTrace(
                round_number=round_number,
                outcome="MODEL_SUCCESS",
                model_called=True,
                usage_complete=True,
                selected_dimensions=[dimension],
            ),
            usage=ModelUsage(
                prompt_tokens=10,
                completion_tokens=2,
                total_tokens=12,
            ),
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


def test_unspecified_candidate_dimensions_use_metric_allowed_dimensions(
    registry: SemanticRegistry,
    database,
    principal: Principal,
):
    service = make_service(
        registry,
        database,
        AnalysisIntent(
            metric_id="paid_gmv",
            question_kind=QuestionKind.INVESTIGATE,
            period=CURRENT,
            comparison_period=PREVIOUS,
        ),
        principal,
        BudgetPolicy(max_queries=4),
    )

    outcome = service.investigate()

    assert outcome.queries_used == 2
    assert outcome.investigation.completed_dimensions == ["region"]
    assert outcome.investigation.stop_reason == "concentrated_contribution"
    assert outcome.claims


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
        BudgetPolicy(max_queries=3),
        initial_queries_used=2,
    )

    outcome = service.investigate()

    assert outcome.queries_used == 2
    assert outcome.investigation.stop_reason == "budget_exhausted"
    assert outcome.investigation.rounds[0].steps == []
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


def test_two_round_planner_uses_round_one_evidence_and_never_third_round(
    registry: SemanticRegistry,
    database,
    principal: Principal,
):
    planner = RecordingPlanner()
    service = make_service(
        registry,
        database,
        AnalysisIntent(
            metric_id="paid_gmv",
            question_kind=QuestionKind.INVESTIGATE,
            period=CURRENT,
            comparison_period=PREVIOUS,
            candidate_dimensions=["channel", "category"],
        ),
        principal,
        BudgetPolicy(max_queries=6),
        planner=planner,
    )

    outcome = service.investigate()

    assert len(planner.calls) == 2
    assert planner.calls[1].round_number == 2
    assert planner.calls[1].evidence
    assert outcome.queries_used == 4
    assert outcome.investigation.stop_reason in {
        "concentrated_contribution",
        "ratio_diagnostic_complete",
        "planner_round_limit",
        "no_remaining_candidates",
    }
    assert len(outcome.investigation.rounds) == 2


def test_second_round_filters_completed_dimensions(
    registry: SemanticRegistry,
    database,
    principal: Principal,
):
    class RepeatPlanner(RecordingPlanner):
        def propose(
            self,
            observation,
            allowed_dimensions,
            *,
            remaining_queries,
            round_number,
        ):
            self.calls.append(observation)
            return PlannerDecision(
                hypotheses=[
                    Hypothesis(
                        dimension_id="channel",
                        rationale_code="repeat",
                        priority=0,
                    )
                ],
                attempt=PlannerAttemptTrace(
                    round_number=round_number,
                    outcome="MODEL_SUCCESS",
                    model_called=True,
                    usage_complete=True,
                    selected_dimensions=["channel"],
                ),
                usage=ModelUsage(),
            )

    planner = RepeatPlanner()
    service = make_service(
        registry,
        database,
        AnalysisIntent(
            metric_id="paid_gmv",
            question_kind=QuestionKind.INVESTIGATE,
            period=CURRENT,
            comparison_period=PREVIOUS,
            candidate_dimensions=["channel", "region"],
        ),
        principal,
        BudgetPolicy(max_queries=6),
        planner=planner,
    )

    outcome = service.investigate()

    assert outcome.queries_used == 2
    assert outcome.investigation.rounds[1].steps == []
    assert outcome.investigation.remaining_dimensions == ["region"]
    assert outcome.investigation.stop_reason == "planner_round_limit"


def test_missing_usage_uses_deterministic_second_round(
    registry: SemanticRegistry,
    database,
    principal: Principal,
):
    calls = 0

    class NoUsagePlanner(RecordingPlanner):
        def propose(self, observation, allowed_dimensions, **kwargs):
            nonlocal calls
            calls += 1
            decision = super().propose(
                observation,
                allowed_dimensions,
                **kwargs,
            )
            attempt = decision.attempt.model_copy(
                update={"usage_complete": False}
            )
            return decision.model_copy(
                update={"attempt": attempt, "usage": None}
            )

    service = make_service(
        registry,
        database,
        AnalysisIntent(
            metric_id="paid_gmv",
            question_kind=QuestionKind.INVESTIGATE,
            period=CURRENT,
            comparison_period=PREVIOUS,
            candidate_dimensions=["channel", "category"],
        ),
        principal,
        BudgetPolicy(max_queries=6),
        planner=NoUsagePlanner(),
    )

    outcome = service.investigate()

    assert calls == 1
    assert outcome.investigation.rounds[1].planner.outcome == "HEURISTIC"
    assert outcome.investigation.rounds[1].planner.model_called is False
    assert outcome.planner_tokens_used is None
    assert outcome.planner_usage_complete is False


def test_failed_current_query_does_not_consume_comparison_budget(
    registry: SemanticRegistry,
    database,
    principal: Principal,
    monkeypatch,
):
    service = additive_service(
        registry,
        database,
        principal,
        BudgetPolicy(max_queries=6),
    )
    calls = 0
    original = service.engine.execute

    def fail_current(compiled, snapshot_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise QueryExecutionError("current failed")
        return original(compiled, snapshot_id)

    monkeypatch.setattr(service.engine, "execute", fail_current)

    outcome = service.investigate()

    assert outcome.queries_used < 2
    assert (
        outcome.investigation.rounds[0].steps[0].status
        is EvidenceStatus.QUERY_FAILED
    )


def test_context_drift_stops_before_planning_and_clears_business_results(
    registry: SemanticRegistry,
    database,
    principal: Principal,
):
    planner = RecordingPlanner()
    service = make_service(
        registry,
        database,
        AnalysisIntent(
            metric_id="paid_gmv",
            question_kind=QuestionKind.INVESTIGATE,
            period=CURRENT,
            comparison_period=PREVIOUS,
            candidate_dimensions=["channel"],
        ),
        principal,
        BudgetPolicy(max_queries=6),
        planner=planner,
    )
    changed_metric = registry.metric("paid_gmv").model_copy(
        update={"display_name": "支付GMV（变更）"}
    )
    registry.metrics["paid_gmv"] = changed_metric

    outcome = service.investigate()

    assert planner.calls == []
    assert outcome.investigation.stop_reason == "context_drift"
    assert outcome.completeness is Completeness.NONE
    assert outcome.claims == []
    assert "CONTEXT_DRIFT" in outcome.issue_codes
