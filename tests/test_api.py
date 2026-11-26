import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from metricledger.api import create_app
from metricledger.data import DuckDbEngine
from metricledger.service import AnalysisService
from metricledger.semantic import SemanticRegistry
from metricledger.snapshots import SnapshotRepository
from scripts.build_demo_data import DEMO_SNAPSHOTS, build_demo_database


TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def client(tmp_path) -> TestClient:
    database = tmp_path / "api.duckdb"
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
    return TestClient(create_app(service))


@pytest.fixture
def request_payload() -> dict:
    return {
        "question": "2026-11-17 到 2026-11-23 支付GMV为什么下降",
        "as_of": "2026-11-23T23:59:59+08:00",
        "principal": {
            "principal_id": "analyst",
            "allowed_values": {
                "region": ["east", "west", "north", "south"],
                "channel": ["app", "web", "mini_program"],
                "category": [
                    "electronics",
                    "beauty",
                    "home",
                    "fashion",
                ],
            },
        },
        "budget": {"max_queries": 4},
    }


def test_analysis_endpoint_returns_claim_and_sql_audit(
    client: TestClient,
    request_payload: dict,
):
    response = client.post("/api/analyses", json=request_payload)

    assert response.status_code == 200
    body = response.json()
    assert body["admission"]["status"] == "ADMITTED"
    assert body["claims"][0]["evidence_refs"]
    assert body["claims"][0]["rendered_text"]
    assert body["sql_audit"]


def test_unknown_analysis_returns_404(client: TestClient):
    assert client.get("/api/analyses/missing").status_code == 404


def test_validation_errors_do_not_echo_authorization_header(
    client: TestClient,
):
    response = client.post(
        "/api/analyses",
        json={},
        headers={"Authorization": "Bearer secret-value"},
    )

    assert response.status_code == 422
    assert "secret-value" not in response.text


def test_health_and_catalog_report_runtime_state(client: TestClient):
    health = client.get("/api/health")
    catalog = client.get("/api/catalog")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["executions"] == 0
    assert catalog.status_code == 200
    assert {
        metric["metric_id"] for metric in catalog.json()["metrics"]
    } == {"paid_gmv", "paid_conversion_rate", "cancel_rate"}


def test_eval_endpoint_returns_a_valid_state(client: TestClient):
    response = client.get("/api/eval/latest")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"COMPLETE", "NOT_RUN", "INVALID"}
    if body["status"] == "COMPLETE":
        assert body["scenario_count"] >= 60


def test_eval_endpoint_reads_configured_report_path(
    client: TestClient,
    tmp_path,
):
    report_path = tmp_path / "results.json"
    report_path.write_text(
        json.dumps(
            {
                "status": "COMPLETE",
                "scenario_count": 63,
            }
        ),
        encoding="utf-8",
    )

    configured_client = TestClient(
        create_app(
            client.app.state.service,
            eval_path=report_path,
        )
    )

    body = configured_client.get("/api/eval/latest").json()
    assert body["status"] == "COMPLETE"
    assert body["scenario_count"] == 63
