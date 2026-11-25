from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from metricledger.api import create_app
from metricledger.data import DuckDbEngine
from metricledger.ledger import SqliteLedger
from metricledger.observability import InMemoryRateLimiter, RequestMetrics
from metricledger.security import ApiKeyAuthenticator
from metricledger.semantic import SemanticRegistry
from metricledger.service import AnalysisService
from metricledger.snapshots import SnapshotRepository
from scripts.build_demo_data import DEMO_SNAPSHOTS, build_demo_database


TZ = ZoneInfo("Asia/Shanghai")
FULL_SCOPE = {
    "region": ["east", "west", "north", "south"],
    "channel": ["app", "web", "mini_program"],
    "category": ["electronics", "beauty", "home", "fashion"],
}


@pytest.fixture
def service(tmp_path) -> AnalysisService:
    database = tmp_path / "tenant.duckdb"
    build_demo_database(database)
    return AnalysisService(
        registry=SemanticRegistry.load_default(),
        snapshots=SnapshotRepository.from_records(DEMO_SNAPSHOTS),
        engine=DuckDbEngine(
            database,
            {
                snapshot["snapshot_id"]: snapshot["table_name"]
                for snapshot in DEMO_SNAPSHOTS
            },
        ),
        ledger=SqliteLedger(tmp_path / "audit.sqlite3"),
    )


@pytest.fixture
def authenticator() -> ApiKeyAuthenticator:
    return ApiKeyAuthenticator.from_mapping(
        {
            "demo-secret": {
                "principal_id": "demo-analyst",
                "tenant_id": "demo",
                "roles": ["viewer", "analyst"],
                "allowed_values": FULL_SCOPE,
            },
            "other-secret": {
                "principal_id": "other-viewer",
                "tenant_id": "tenant_b",
                "roles": ["viewer"],
                "allowed_values": FULL_SCOPE,
            },
        }
    )


def submission(
    *,
    tenant_id: str = "tenant_b",
    region: list[str] | None = None,
) -> dict:
    return {
        "question": "2026-11-17 到 2026-11-23 支付GMV为什么下降",
        "as_of": datetime(
            2026, 11, 23, 23, 59, 59, tzinfo=TZ
        ).isoformat(),
        "principal": {
            "principal_id": "forged-client",
            "tenant_id": tenant_id,
            "allowed_values": {
                "region": region or ["east"],
                "channel": ["app"],
                "category": ["electronics"],
            },
        },
        "budget": {"max_queries": 4},
    }


def test_authenticated_principal_overrides_forged_request_principal(
    service: AnalysisService,
    authenticator: ApiKeyAuthenticator,
):
    client = TestClient(
        create_app(
            service,
            authenticator=authenticator,
            rate_limiter=InMemoryRateLimiter(limit=100),
        )
    )

    response = client.post(
        "/api/analyses",
        json=submission(tenant_id="tenant_b", region=["east"]),
        headers={"Authorization": "Bearer demo-secret"},
    )

    assert response.status_code == 200
    context = response.json()["context"]
    assert context["tenant_id"] == "demo"
    assert set(context["effective_scope"]["values"]["region"]) == {
        "east",
        "west",
        "north",
        "south",
    }


def test_cross_tenant_analysis_lookup_is_indistinguishable_from_missing(
    service: AnalysisService,
    authenticator: ApiKeyAuthenticator,
):
    client = TestClient(
        create_app(
            service,
            authenticator=authenticator,
            rate_limiter=InMemoryRateLimiter(limit=100),
        )
    )
    created = client.post(
        "/api/analyses",
        json=submission(),
        headers={"Authorization": "Bearer demo-secret"},
    )
    analysis_id = created.json()["analysis_id"]

    response = client.get(
        f"/api/analyses/{analysis_id}",
        headers={"Authorization": "Bearer other-secret"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "analysis not found"
    assert analysis_id not in response.text or response.json()["detail"] == (
        "analysis not found"
    )


def test_viewer_cannot_create_analysis(
    service: AnalysisService,
    authenticator: ApiKeyAuthenticator,
):
    client = TestClient(
        create_app(service, authenticator=authenticator)
    )

    response = client.post(
        "/api/analyses",
        json=submission(),
        headers={"Authorization": "Bearer other-secret"},
    )

    assert response.status_code == 403
    assert "analyst" in response.json()["detail"]


def test_missing_or_invalid_bearer_token_is_rejected(
    service: AnalysisService,
    authenticator: ApiKeyAuthenticator,
):
    client = TestClient(
        create_app(service, authenticator=authenticator)
    )

    missing = client.get("/api/catalog")
    invalid = client.get(
        "/api/catalog",
        headers={"Authorization": "Bearer not-a-key"},
    )

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert invalid.status_code == 401
    assert "not-a-key" not in invalid.text


def test_rate_limit_is_scoped_to_authenticated_principal(
    service: AnalysisService,
    authenticator: ApiKeyAuthenticator,
):
    client = TestClient(
        create_app(
            service,
            authenticator=authenticator,
            rate_limiter=InMemoryRateLimiter(
                limit=1,
                window_seconds=60,
            ),
        )
    )
    headers = {"Authorization": "Bearer demo-secret"}

    first = client.get("/api/catalog", headers=headers)
    second = client.get("/api/catalog", headers=headers)
    other_principal = client.get(
        "/api/catalog",
        headers={"Authorization": "Bearer other-secret"},
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert int(second.headers["retry-after"]) >= 1
    assert other_principal.status_code == 200


def test_request_id_and_prometheus_metrics_are_observable(
    service: AnalysisService,
):
    metrics = RequestMetrics()
    client = TestClient(
        create_app(service, metrics=metrics)
    )

    health = client.get(
        "/api/health",
        headers={"X-Request-ID": "req-tenant-test-123"},
    )
    response = client.get("/api/metrics")

    assert health.headers["x-request-id"] == "req-tenant-test-123"
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "metricledger_http_requests_total" in response.text
    assert "metricledger_http_request_duration_seconds_bucket" in response.text
    assert "metricledger_duckdb_queries_total" in response.text
    assert "metricledger_sqlite_ledger_writes_total" in response.text


def test_prometheus_metrics_track_analysis_issue_codes(
    service: AnalysisService,
):
    metrics = RequestMetrics()
    client = TestClient(
        create_app(service, metrics=metrics)
    )

    analysis = client.post(
        "/api/analyses",
        json={
            "question": "支付GMV是多少",
            "as_of": datetime(
                2026, 11, 23, 23, 59, 59, tzinfo=TZ
            ).isoformat(),
            "principal": {
                "principal_id": "metric-test",
                "allowed_values": FULL_SCOPE,
            },
            "budget": {"max_queries": 4},
        },
    )
    response = client.get("/api/metrics")

    assert analysis.status_code == 200
    assert (
        'metricledger_analysis_issues_total{admission="CLARIFY",'
        'code="MISSING_PERIOD",severity="ERROR"} 1'
    ) in response.text


def test_w3c_trace_context_is_propagated_to_response(
    service: AnalysisService,
):
    client = TestClient(create_app(service))
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    parent_span_id = "00f067aa0ba902b7"

    response = client.get(
        "/api/health",
        headers={
            "traceparent": (
                f"00-{trace_id}-{parent_span_id}-01"
            )
        },
    )

    assert response.status_code == 200
    assert response.headers["x-trace-id"] == trace_id
    assert response.headers["traceparent"].startswith(f"00-{trace_id}-")
    assert response.headers["traceparent"].endswith("-01")


def test_invalid_trace_context_is_replaced_with_a_local_trace(
    service: AnalysisService,
):
    client = TestClient(create_app(service))

    response = client.get(
        "/api/health",
        headers={"traceparent": "not-a-valid-traceparent"},
    )

    traceparent = response.headers["traceparent"]
    version, trace_id, span_id, flags = traceparent.split("-")
    assert version == "00"
    assert len(trace_id) == 32
    assert len(span_id) == 16
    assert flags == "01"
    assert trace_id != "0" * 32
    assert span_id != "0" * 16
