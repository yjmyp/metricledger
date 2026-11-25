from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from metricledger.compiler import QueryCompiler
from metricledger.guards import SqlGuardError, validate_select
from metricledger.models import AnalysisIntent, DateRange, QuestionKind, Scope, SnapshotRecord
from metricledger.semantic import SemanticRegistry
from tests.test_intent import registry_data


TZ = ZoneInfo("Asia/Shanghai")
SNAPSHOT = SnapshotRecord(
    snapshot_id="batch_20261123_2300",
    table_name="sales_batch_20261123_2300",
    reporting_as_of=datetime(2026, 11, 23, 23, 59, 59, tzinfo=TZ),
    period_start=date(2026, 10, 1),
    period_end=date(2026, 11, 23),
    manifest_hash="manifest-1123",
)


@pytest.fixture
def compiler() -> QueryCompiler:
    registry = SemanticRegistry.from_mapping(registry_data())
    return QueryCompiler(registry)


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


def test_compiler_binds_times_and_scope_as_parameters(compiler: QueryCompiler, intent):
    metric = compiler.registry.metric("paid_gmv")
    compiled = compiler.compile(
        intent,
        metric,
        SNAPSHOT,
        Scope(
            tenant_id="demo",
            values={"region": ["east", "west"], "channel": ["app", "web"]},
        ),
    )

    assert compiled.sql.startswith("SELECT")
    assert "?" in compiled.sql
    assert "east" not in compiled.sql
    assert "sales_batch_20261123_2300" in compiled.sql
    assert compiled.parameters[:2] == ["2026-11-17", "2026-11-23"]
    assert compiled.parameters[-1] == "demo"
    assert "tenant_id = ?" in compiled.sql
    assert "demo" not in compiled.sql
    assert compiled.referenced_tables == ["sales_batch_20261123_2300"]
    assert compiled.fingerprint


def test_compiler_uses_requested_filter_values_only(compiler: QueryCompiler, intent):
    filtered = intent.model_copy(update={"filters": {"region": ["west"]}})
    compiled = compiler.compile(
        filtered,
        compiler.registry.metric("paid_gmv"),
        SNAPSHOT,
        Scope(values={"region": ["east", "west"], "channel": ["app", "web"]}),
    )

    assert "west" not in compiled.sql
    assert "west" in compiled.parameters
    assert "east" not in compiled.parameters


def test_guard_rejects_multi_statement_sql():
    with pytest.raises(SqlGuardError, match="single statement"):
        validate_select(
            "SELECT 1; DROP TABLE sales_batch_20261123_2300",
            allowed_tables={"sales_batch_20261123_2300"},
            allowed_columns={"event_date"},
        )


def test_guard_rejects_dml_and_ddl():
    with pytest.raises(SqlGuardError, match="read-only SELECT"):
        validate_select(
            "DELETE FROM sales_batch_20261123_2300",
            allowed_tables={"sales_batch_20261123_2300"},
            allowed_columns={"event_date"},
        )


def test_guard_rejects_unknown_table_and_column():
    with pytest.raises(SqlGuardError, match="table"):
        validate_select(
            "SELECT secret FROM other_table",
            allowed_tables={"sales_batch_20261123_2300"},
            allowed_columns={"secret"},
        )

    with pytest.raises(SqlGuardError, match="column"):
        validate_select(
            "SELECT secret FROM sales_batch_20261123_2300",
            allowed_tables={"sales_batch_20261123_2300"},
            allowed_columns={"event_date"},
        )


def test_guard_rejects_external_or_unknown_function_nodes():
    with pytest.raises(SqlGuardError, match="function"):
        validate_select(
            "SELECT read_blob('/etc/passwd') FROM sales_batch_20261123_2300",
            allowed_tables={"sales_batch_20261123_2300"},
            allowed_columns={"event_date"},
        )


def test_guard_rejects_typed_function_nodes():
    with pytest.raises(SqlGuardError, match="function"):
        validate_select(
            "SELECT LOWER(event_date) FROM sales_batch_20261123_2300",
            allowed_tables={"sales_batch_20261123_2300"},
            allowed_columns={"event_date"},
        )


def test_guard_rejects_nested_selects():
    with pytest.raises(SqlGuardError, match="nested"):
        validate_select(
            (
                "SELECT SUM(metric_value) FROM "
                "(SELECT paid_gmv AS metric_value "
                "FROM sales_batch_20261123_2300) AS nested"
            ),
            allowed_tables={"sales_batch_20261123_2300"},
            allowed_columns={"paid_gmv", "metric_value"},
        )


def test_guard_rejects_joins():
    with pytest.raises(SqlGuardError, match="join"):
        validate_select(
            (
                "SELECT SUM(f.paid_gmv) FROM sales_batch_20261123_2300 AS f "
                "JOIN sales_batch_20261123_2300 AS g "
                "ON f.event_date = g.event_date"
            ),
            allowed_tables={"sales_batch_20261123_2300"},
            allowed_columns={"paid_gmv", "event_date"},
        )


def test_guard_requires_exactly_one_source_table():
    with pytest.raises(SqlGuardError, match="exactly one"):
        validate_select(
            (
                "SELECT SUM(first_value) "
                "FROM first_snapshot AS first, second_snapshot AS second"
            ),
            allowed_tables={"first_snapshot", "second_snapshot"},
            allowed_columns={"first_value"},
        )


def test_valid_compiler_sql_passes_guard(compiler: QueryCompiler, intent):
    compiled = compiler.compile(
        intent,
        compiler.registry.metric("paid_gmv"),
        SNAPSHOT,
        Scope(values={"region": ["east", "west"], "channel": ["app", "web"]}),
    )

    validate_select(
        compiled.sql,
        allowed_tables={"sales_batch_20261123_2300"},
        allowed_columns={
            "event_date",
            "region",
            "channel",
            "category",
            "paid_orders",
            "sessions",
            "canceled_orders",
            "paid_gmv",
                "tenant_id",
            "metric_value",
        },
    )
