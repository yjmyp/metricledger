from datetime import date, datetime
from zoneinfo import ZoneInfo

from metricledger.models import DateRange, QualityStatus, SnapshotRecord
from metricledger.quality import QualityService
from metricledger.semantic import SemanticRegistry


TZ = ZoneInfo("Asia/Shanghai")


def snapshot(**overrides) -> SnapshotRecord:
    values = {
        "snapshot_id": "batch_20261123_2300",
        "table_name": "sales_batch_20261123_2300",
        "reporting_as_of": datetime(2026, 11, 23, 23, 59, 59, tzinfo=TZ),
        "period_start": date(2026, 10, 1),
        "period_end": date(2026, 11, 23),
        "complete": True,
        "duplicate_rate": 0.0,
        "null_rate": 0.0,
        "manifest_hash": "manifest-1123",
    }
    values.update(overrides)
    return SnapshotRecord(**values)


def test_ratio_quality_blocks_zero_denominator():
    registry = SemanticRegistry.load_default()
    checks = QualityService(registry).evaluate(
        registry.metric("paid_conversion_rate"),
        snapshot(),
        DateRange(start=date(2026, 11, 17), end=date(2026, 11, 23)),
        denominator_positive=False,
    )

    denominator = next(check for check in checks if check.code == "POSITIVE_DENOMINATOR")
    assert denominator.status is QualityStatus.BLOCK


def test_quality_warns_on_missing_period_coverage():
    registry = SemanticRegistry.load_default()
    checks = QualityService(registry).evaluate(
        registry.metric("paid_gmv"),
        snapshot(period_end=date(2026, 11, 20)),
        DateRange(start=date(2026, 11, 17), end=date(2026, 11, 23)),
    )

    coverage = next(check for check in checks if check.code == "DATE_COVERAGE")
    assert coverage.status is QualityStatus.WARN


def test_quality_blocks_incomplete_snapshot_for_all_metrics():
    registry = SemanticRegistry.load_default()
    checks = QualityService(registry).evaluate(
        registry.metric("paid_gmv"),
        snapshot(complete=False),
        DateRange(start=date(2026, 11, 17), end=date(2026, 11, 23)),
    )

    complete = next(check for check in checks if check.code == "SNAPSHOT_COMPLETE")
    assert complete.status is QualityStatus.BLOCK
