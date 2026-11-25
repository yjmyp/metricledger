from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from metricledger.data import DuckDbEngine
from metricledger.models import (
    AdmissionStatus,
    AnalysisRequest,
    BudgetPolicy,
    ClaimSupport,
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
