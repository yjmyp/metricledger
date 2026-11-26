from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from metricledger.admission import AdmissionService
from metricledger.context_guard import ContextIntegrityError, ContextIntegrityGuard
from metricledger.models import AnalysisIntent, DateRange, Principal, QuestionKind
from metricledger.semantic import SemanticRegistry
from metricledger.snapshots import SnapshotRepository
from scripts.build_demo_data import DEMO_SNAPSHOTS


def admitted_context(registry):
    principal = Principal(
        principal_id="guard",
        allowed_values={
            dimension.dimension_id: list(dimension.values)
            for dimension in registry.bundle.dimensions
        },
    )
    decision = AdmissionService(
        registry,
        SnapshotRepository.from_records(DEMO_SNAPSHOTS),
    ).admit(
        AnalysisIntent(
            metric_id="paid_gmv",
            question_kind=QuestionKind.INVESTIGATE,
            period=DateRange(start=date(2026, 11, 17), end=date(2026, 11, 23)),
            comparison_period=DateRange(
                start=date(2026, 11, 10),
                end=date(2026, 11, 16),
            ),
        ),
        principal,
        as_of=datetime(
            2026,
            11,
            23,
            23,
            59,
            59,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ),
    )
    assert decision.context is not None
    return decision.context


def test_guard_accepts_unchanged_context():
    registry = SemanticRegistry.load_default()
    guard = ContextIntegrityGuard(
        registry,
        SnapshotRepository.from_records(DEMO_SNAPSHOTS),
    )

    guard.verify(admitted_context(registry))


def test_guard_rejects_semantic_drift():
    registry = SemanticRegistry.load_default()
    context = admitted_context(registry)
    changed_metric = registry.metric("paid_gmv").model_copy(
        update={"display_name": "支付GMV（变更）"}
    )
    registry.metrics["paid_gmv"] = changed_metric
    guard = ContextIntegrityGuard(
        registry,
        SnapshotRepository.from_records(DEMO_SNAPSHOTS),
    )

    with pytest.raises(ContextIntegrityError, match="semantic"):
        guard.verify(context)


def test_guard_rejects_snapshot_manifest_drift():
    registry = SemanticRegistry.load_default()
    context = admitted_context(registry)
    records = [dict(record) for record in DEMO_SNAPSHOTS]
    records[-1]["manifest_hash"] = "changed-manifest"
    guard = ContextIntegrityGuard(
        registry,
        SnapshotRepository.from_records(records),
    )

    with pytest.raises(ContextIntegrityError, match="snapshot"):
        guard.verify(context)
