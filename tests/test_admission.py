from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from metricledger.admission import AdmissionService
from metricledger.models import (
    AdmissionStatus,
    AnalysisIntent,
    BudgetPolicy,
    DateRange,
    Principal,
    QuestionKind,
)
from metricledger.semantic import SemanticRegistry
from metricledger.snapshots import SnapshotRepository
from tests.test_semantic import registry_data

TZ = ZoneInfo("Asia/Shanghai")
DEMO_AS_OF = datetime(2026, 11, 23, 23, 59, 59, tzinfo=TZ)


@pytest.fixture
def repository() -> SnapshotRepository:
    return SnapshotRepository.from_records(
        [
            {
                "snapshot_id": "batch_20261116_2300",
                "table_name": "sales_batch_20261116_2300",
                "reporting_as_of": "2026-11-16T23:59:59+08:00",
                "period_start": "2026-10-01",
                "period_end": "2026-11-16",
                "complete": True,
                "duplicate_rate": 0,
                "null_rate": 0,
                "manifest_hash": "manifest-1116",
            },
            {
                "snapshot_id": "batch_20261123_2300",
                "table_name": "sales_batch_20261123_2300",
                "reporting_as_of": "2026-11-23T23:59:59+08:00",
                "period_start": "2026-10-01",
                "period_end": "2026-11-23",
                "complete": True,
                "duplicate_rate": 0,
                "null_rate": 0,
                "manifest_hash": "manifest-1123",
            },
        ]
    )


@pytest.fixture
def service(repository: SnapshotRepository) -> AdmissionService:
    return AdmissionService(SemanticRegistry.from_mapping(registry_data()), repository)


@pytest.fixture
def intent() -> AnalysisIntent:
    return AnalysisIntent(
        metric_id="paid_gmv",
        question_kind=QuestionKind.INVESTIGATE,
        period=DateRange(start=date(2026, 11, 17), end=date(2026, 11, 23)),
        comparison_period=DateRange(
            start=date(2026, 11, 10), end=date(2026, 11, 16)
        ),
    )


@pytest.fixture
def full_principal() -> Principal:
    return Principal(
        principal_id="demo",
        allowed_values={"region": ["east", "west", "north", "south"]},
    )


def test_admission_selects_latest_complete_snapshot(
    service: AdmissionService, intent: AnalysisIntent, full_principal: Principal
):
    decision = service.admit(intent, full_principal, as_of=DEMO_AS_OF)

    assert decision.status is AdmissionStatus.ADMITTED
    assert decision.snapshot is not None
    assert decision.snapshot.snapshot_id == "batch_20261123_2300"
    assert decision.context is not None


def test_admission_rejects_snapshot_that_does_not_cover_comparison(
    service: AdmissionService, full_principal: Principal
):
    incomplete = AnalysisIntent(
        metric_id="paid_gmv",
        question_kind=QuestionKind.CHANGE,
        period=DateRange(start=date(2026, 10, 1), end=date(2026, 10, 2)),
        comparison_period=DateRange(
            start=date(2026, 9, 30), end=date(2026, 10, 1)
        ),
    )

    decision = service.admit(incomplete, full_principal, as_of=DEMO_AS_OF)

    assert decision.status is AdmissionStatus.UNAVAILABLE
    assert decision.issue_codes == ["COMPARISON_NOT_COVERED"]


def test_admission_refuses_implicit_scope_narrowing(
    service: AdmissionService, intent: AnalysisIntent
):
    east_only = Principal(
        principal_id="east-only",
        allowed_values={"region": ["east"]},
    )

    decision = service.admit(intent, east_only, as_of=DEMO_AS_OF)

    assert decision.status is AdmissionStatus.REFUSED
    assert decision.issue_codes == ["SCOPE_NOT_AUTHORIZED"]


def test_admission_rejects_requested_value_outside_scope(
    service: AdmissionService, full_principal: Principal
):
    east_only = Principal(
        principal_id="east-only",
        allowed_values={"region": ["east"]},
    )
    request = AnalysisIntent(
        metric_id="paid_gmv",
        question_kind=QuestionKind.CHANGE,
        period=DateRange(start=date(2026, 11, 17), end=date(2026, 11, 23)),
        comparison_period=DateRange(
            start=date(2026, 11, 10), end=date(2026, 11, 16)
        ),
        filters={"region": ["west"]},
    )

    assert service.admit(request, full_principal, as_of=DEMO_AS_OF).status is (
        AdmissionStatus.ADMITTED
    )
    assert service.admit(request, east_only, as_of=DEMO_AS_OF).status is (
        AdmissionStatus.REFUSED
    )


def test_stale_snapshot_is_unavailable(
    repository: SnapshotRepository,
    intent: AnalysisIntent,
    full_principal: Principal,
):
    semantic = SemanticRegistry.from_mapping(registry_data())
    service = AdmissionService(semantic, repository)
    later = datetime(2026, 11, 25, 23, 59, 59, tzinfo=TZ)

    decision = service.admit(intent, full_principal, as_of=later)

    assert decision.status is AdmissionStatus.UNAVAILABLE
    assert "SNAPSHOT_STALE" in decision.issue_codes
