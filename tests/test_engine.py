from datetime import date

import pytest

from metricledger.compiler import QueryCompiler
from metricledger.data import DuckDbEngine, UnknownSnapshotError
from metricledger.models import AnalysisIntent, DateRange, QuestionKind, Scope, SnapshotRecord
from metricledger.semantic import SemanticRegistry
from scripts.build_demo_data import DEMO_SNAPSHOTS, build_demo_database


@pytest.fixture
def registry() -> SemanticRegistry:
    return SemanticRegistry.load_default()


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "metricledger.duckdb"
    build_demo_database(path)
    return path


@pytest.fixture
def snapshot(registry: SemanticRegistry) -> SnapshotRecord:
    record = next(
        item for item in DEMO_SNAPSHOTS if item["snapshot_id"] == "batch_20261123_2300"
    )
    return SnapshotRecord.model_validate(record)


@pytest.fixture
def compiled(registry: SemanticRegistry, snapshot: SnapshotRecord):
    intent = AnalysisIntent(
        metric_id="paid_gmv",
        question_kind=QuestionKind.VALUE,
        period=DateRange(start=date(2026, 11, 17), end=date(2026, 11, 23)),
    )
    scope = Scope(
        values={
            "region": ["east", "west", "north", "south"],
            "channel": ["app", "web", "mini_program"],
            "category": ["electronics", "beauty", "home", "fashion"],
        }
    )
    return QueryCompiler(registry).compile(
        intent,
        registry.metric("paid_gmv"),
        snapshot,
        scope,
    )


def test_duckdb_engine_executes_against_fixed_snapshot(database, compiled):
    engine = DuckDbEngine(
        database,
        {"batch_20261123_2300": "sales_batch_20261123_2300"},
    )

    rows = engine.execute(compiled, "batch_20261123_2300")

    assert rows.row_count == 1
    assert rows.rows[0]["metric_value"] == pytest.approx(732400.0)


def test_engine_rejects_unknown_snapshot(database, compiled):
    engine = DuckDbEngine(
        database,
        {"batch_20261123_2300": "sales_batch_20261123_2300"},
    )

    with pytest.raises(UnknownSnapshotError):
        engine.execute(compiled, "missing_snapshot")


def test_empty_success_is_not_the_same_as_business_zero(
    database, registry: SemanticRegistry, snapshot: SnapshotRecord
):
    empty_intent = AnalysisIntent(
        metric_id="paid_gmv",
        question_kind=QuestionKind.VALUE,
        period=DateRange(start=date(2026, 12, 1), end=date(2026, 12, 2)),
    )
    compiled = QueryCompiler(registry).compile(
        empty_intent,
        registry.metric("paid_gmv"),
        snapshot,
        Scope(
            values={
                "region": ["east"],
                "channel": ["app"],
                "category": ["electronics"],
            }
        ),
        group_by="region",
    )
    engine = DuckDbEngine(
        database,
        {"batch_20261123_2300": "sales_batch_20261123_2300"},
    )

    rows = engine.execute(compiled, "batch_20261123_2300")

    assert rows.rows == []
    assert rows.row_count == 0


def test_engine_rejects_sql_that_drifted_from_the_fixed_snapshot(
    database, registry: SemanticRegistry, snapshot: SnapshotRecord
):
    intent = AnalysisIntent(
        metric_id="paid_gmv",
        question_kind=QuestionKind.VALUE,
        period=DateRange(start=date(2026, 11, 17), end=date(2026, 11, 23)),
    )
    compiled = QueryCompiler(registry).compile(
        intent,
        registry.metric("paid_gmv"),
        snapshot,
        Scope(
            values={
                "region": ["east"],
                "channel": ["app"],
                "category": ["electronics"],
            }
        ),
    )
    drifted = compiled.model_copy(
        update={
            "referenced_tables": ["sales_batch_20261116_2300"],
        }
    )
    engine = DuckDbEngine(
        database,
        {"batch_20261123_2300": "sales_batch_20261123_2300"},
    )

    with pytest.raises(ValueError, match="snapshot"):
        engine.execute(drifted, "batch_20261123_2300")
