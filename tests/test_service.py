from datetime import datetime
from zoneinfo import ZoneInfo

import duckdb
import pytest

from metricledger.data import DuckDbEngine
from metricledger.models import (
    AdmissionStatus,
    AnalysisRequest,
    BudgetPolicy,
    ClaimSupport,
    Completeness,
    Principal,
)
from metricledger.service import AnalysisService
from metricledger.semantic import SemanticRegistry
from metricledger.snapshots import SnapshotRepository
from scripts.build_demo_data import DEMO_SNAPSHOTS, build_demo_database


TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 11, 23, 23, 59, 59, tzinfo=TZ)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "service.duckdb"
    build_demo_database(path)
    return path


@pytest.fixture
def registry() -> SemanticRegistry:
    return SemanticRegistry.load_default()


@pytest.fixture
def engine(database) -> DuckDbEngine:
    return DuckDbEngine(
        database,
        {
            snapshot["snapshot_id"]: snapshot["table_name"]
            for snapshot in DEMO_SNAPSHOTS
        },
    )


@pytest.fixture
def service(registry: SemanticRegistry, engine: DuckDbEngine) -> AnalysisService:
    return AnalysisService(
        registry=registry,
        snapshots=SnapshotRepository.from_records(DEMO_SNAPSHOTS),
        engine=engine,
    )


@pytest.fixture
def analysis_request() -> AnalysisRequest:
    return AnalysisRequest(
        question="2026-11-17 到 2026-11-23 支付GMV为什么下降",
        as_of=AS_OF,
        principal=Principal(
            principal_id="analyst",
            allowed_values={
                "region": ["east", "west", "north", "south"],
                "channel": ["app", "web", "mini_program"],
                "category": ["electronics", "beauty", "home", "fashion"],
            },
        ),
        budget=BudgetPolicy(max_queries=4),
    )


def test_service_returns_supported_answer_with_audit_sql(
    service: AnalysisService,
    analysis_request: AnalysisRequest,
):
    response = service.analyze(analysis_request)

    assert response.admission.status is AdmissionStatus.ADMITTED
    assert response.claims
    assert all(
        claim.claim.support is ClaimSupport.SUPPORTED
        for claim in response.claims
    )
    assert response.sql_audit[0].sql.startswith("SELECT")
    assert response.answer


def test_headline_claim_renders_every_evidence_placeholder(
    service: AnalysisService,
    analysis_request: AnalysisRequest,
):
    response = service.analyze(analysis_request)

    rendered = response.claims[0].rendered_text

    assert "{evidence:" not in rendered
    assert "806,400.00" in rendered
    assert "732,400.00" in rendered
    assert "-9.18%" in rendered


def test_service_rejects_unsafe_scope_without_running_sql(
    service: AnalysisService,
    analysis_request: AnalysisRequest,
    engine: DuckDbEngine,
):
    east_only_request = analysis_request.model_copy(
        update={
            "principal": Principal(
                principal_id="east-only",
                allowed_values={"region": ["east"]},
            )
        }
    )
    before = engine.execution_count

    response = service.analyze(east_only_request)

    assert response.admission.status is AdmissionStatus.REFUSED
    assert engine.execution_count == before
    assert response.claims == []
    assert any(
        issue.message == "请求范围超出权限" for issue in response.issues
    )


def test_empty_period_is_unavailable_without_becoming_zero(
    service: AnalysisService,
    analysis_request: AnalysisRequest,
    engine: DuckDbEngine,
):
    outside = analysis_request.model_copy(
        update={
            "question": "2026-12-01 到 2026-12-02 支付GMV是多少",
        }
    )
    before = engine.execution_count

    response = service.analyze(outside)

    assert response.admission.status is AdmissionStatus.UNAVAILABLE
    assert response.completeness.value == "NONE"
    assert response.answer is None
    assert engine.execution_count == before


def test_service_does_not_execute_comparison_beyond_query_budget(
    service: AnalysisService,
    analysis_request: AnalysisRequest,
    engine: DuckDbEngine,
):
    before = engine.execution_count
    one_query_request = analysis_request.model_copy(
        update={"budget": BudgetPolicy(max_queries=1)}
    )

    response = service.analyze(one_query_request)

    assert response.admission.status is AdmissionStatus.ADMITTED
    assert response.budget_usage.queries_used == 1
    assert engine.execution_count == before + 1
    assert len(response.sql_audit) == 1


def test_zero_ratio_denominator_blocks_headline_claim(
    service: AnalysisService,
    analysis_request: AnalysisRequest,
    database,
):
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            """
            UPDATE sales_batch_20261123_2300
            SET sessions = 0
            WHERE event_date BETWEEN DATE '2026-11-17' AND DATE '2026-11-23'
            """
        )
    request = analysis_request.model_copy(
        update={
            "question": "2026-11-17 到 2026-11-23 支付转化率为什么下降",
        }
    )

    response = service.analyze(request)

    assert response.admission.status is AdmissionStatus.ADMITTED
    assert any(
        issue.code == "POSITIVE_DENOMINATOR" for issue in response.issues
    )
    assert response.claims == []
    assert response.answer is None


def test_zero_baseline_ratio_denominator_blocks_change_claim(
    service: AnalysisService,
    analysis_request: AnalysisRequest,
    database,
):
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            """
            UPDATE sales_batch_20261123_2300
            SET sessions = 0
            WHERE event_date BETWEEN DATE '2026-11-10' AND DATE '2026-11-16'
            """
        )
    request = analysis_request.model_copy(
        update={
            "question": "2026-11-17 到 2026-11-23 支付转化率为什么下降",
        }
    )

    response = service.analyze(request)

    assert response.admission.status is AdmissionStatus.ADMITTED
    assert any(
        issue.code == "POSITIVE_DENOMINATOR" for issue in response.issues
    )
    assert response.claims == []
    assert response.answer is None


def test_service_exposes_investigation_trace_and_planner_usage(
    service: AnalysisService,
    analysis_request: AnalysisRequest,
):
    response = service.analyze(
        analysis_request.model_copy(
            update={"budget": BudgetPolicy(max_queries=6)}
        )
    )

    assert response.investigation is not None
    assert response.investigation.stop_reason
    assert len(response.investigation.rounds) >= 1
    assert response.budget_usage.planner_usage_complete is True
    assert response.budget_usage.planner_tokens_used == 0
    assert response.budget_usage.elapsed_ms >= 0


def test_scalar_context_drift_blocks_sql_without_an_investigation_trace(
    service: AnalysisService,
    analysis_request: AnalysisRequest,
    engine: DuckDbEngine,
    monkeypatch,
):
    from metricledger.context_guard import ContextIntegrityError

    def drifted(self, context):
        raise ContextIntegrityError("semantic")

    monkeypatch.setattr(
        service.guard,
        "verify",
        drifted.__get__(service.guard, type(service.guard)),
    )
    before = engine.execution_count
    request = analysis_request.model_copy(
        update={"question": "2026-11-17 到 2026-11-23 支付GMV是多少"}
    )

    response = service.analyze(request)

    assert engine.execution_count == before
    assert response.issues[0].code == "CONTEXT_DRIFT"
    assert response.investigation is None


def test_drift_clears_answer_and_claims_but_keeps_sql_audit(
    service: AnalysisService,
    analysis_request: AnalysisRequest,
    monkeypatch,
):
    from metricledger.context_guard import ContextIntegrityError

    verifications = 0

    def drifted(self, context):
        nonlocal verifications
        verifications += 1
        if verifications >= 2:
            raise ContextIntegrityError("snapshot")

    monkeypatch.setattr(
        service.guard,
        "verify",
        drifted.__get__(service.guard, type(service.guard)),
    )

    response = service.analyze(analysis_request)

    assert response.completeness is Completeness.NONE
    assert response.answer is None
    assert response.claims == []
    assert any(issue.code == "CONTEXT_DRIFT" for issue in response.issues)
    assert response.sql_audit
    assert response.investigation.stop_reason == "context_drift"
