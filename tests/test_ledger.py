from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from metricledger.data import DuckDbEngine
from metricledger.ledger import SqliteLedger
from metricledger.models import AnalysisRequest, Principal
from metricledger.service import AnalysisService
from metricledger.semantic import SemanticRegistry
from metricledger.snapshots import SnapshotRepository
from scripts.build_demo_data import DEMO_SNAPSHOTS, build_demo_database


TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 11, 23, 23, 59, 59, tzinfo=TZ)


@pytest.fixture
def ledger(tmp_path) -> SqliteLedger:
    return SqliteLedger(tmp_path / "audit.sqlite3")


@pytest.fixture
def response(tmp_path):
    database = tmp_path / "ledger.duckdb"
    build_demo_database(database)
    service = AnalysisService(
        registry=SemanticRegistry.load_default(),
        snapshots=SnapshotRepository.from_records(DEMO_SNAPSHOTS),
        engine=DuckDbEngine(
            database,
            {
                snapshot["snapshot_id"]: snapshot["table_name"]
                for snapshot in DEMO_SNAPSHOTS
            },
        ),
    )
    request = AnalysisRequest(
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
    )
    return service.analyze(request)


def test_ledger_round_trips_response_without_api_key(ledger, response):
    ledger.save(response)

    loaded = ledger.get(response.analysis_id)

    assert loaded is not None
    assert loaded.model_dump() == response.model_dump()
    assert "api_key" not in ledger.dump_all_text().lower()


def test_ledger_creates_claim_evidence_edges(ledger, response):
    ledger.save(response)

    edge_count = ledger.edge_count(response.analysis_id)

    assert edge_count >= len(response.claims)


def test_ledger_rejects_replacing_a_saved_analysis(ledger, response):
    ledger.save(response)
    tampered = response.model_copy(update={"answer": "replacement"})

    with pytest.raises(ValueError, match="immutable"):
        ledger.save(tampered)

    loaded = ledger.get(response.analysis_id)
    assert loaded is not None
    assert loaded.answer == response.answer


def test_ledger_rejects_artifact_replacement_atomically(ledger, response):
    ledger.save(response)
    tampered_artifact = response.evidence[0].model_copy(
        update={"compiled_sql": "SELECT 'replacement'"}
    )
    second = response.model_copy(
        update={
            "analysis_id": "an_second",
            "claims": [],
            "evidence": [tampered_artifact],
            "sql_audit": [],
        }
    )

    with pytest.raises(ValueError, match="immutable"):
        ledger.save(second)

    assert ledger.get("an_second") is None


def test_ledger_lookup_is_tenant_scoped(ledger, response):
    ledger.save(response)

    assert ledger.get(response.analysis_id, tenant_id="demo") is not None
    assert ledger.get(response.analysis_id, tenant_id="tenant_b") is None


def test_ledger_records_write_and_read_latency_counters(ledger, response):
    ledger.save(response)
    assert ledger.get(response.analysis_id, tenant_id="demo") is not None

    assert ledger.write_count == 1
    assert ledger.write_error_count == 0
    assert ledger.total_write_ms >= 0
    assert ledger.read_count == 1
    assert ledger.read_error_count == 0
    assert ledger.total_read_ms >= 0


def test_identical_analysis_write_is_idempotent(ledger, response):
    ledger.save(response)
    ledger.save(response)

    assert ledger.write_count == 2
    assert ledger.write_error_count == 0


def test_concurrent_identical_analysis_writes_are_serialized(
    ledger,
    response,
):
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(ledger.save, response) for _ in range(2)]
        for future in futures:
            future.result()

    assert ledger.get(response.analysis_id) is not None
    assert ledger.write_error_count == 0


def test_concurrent_identical_artifact_writes_create_one_row(
    ledger,
    response,
):
    second = response.model_copy(
        update={
            "analysis_id": "an_second",
            "claims": [],
            "sql_audit": [],
        }
    )
    second = second.model_copy(
        update={"evidence": [response.evidence[0]]}
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(ledger.save, item) for item in (response, second)
        ]
        for future in futures:
            future.result()

    with ledger._connect() as connection:
        count = connection.execute(
            "SELECT count(*) FROM result_artifacts WHERE result_id = ?",
            (response.evidence[0].result_id,),
        ).fetchone()[0]
    assert count == 1
