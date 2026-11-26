from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.request import Request, urlopen


ANALYSIS_REQUEST = {
    "question": "2026-11-17 到 2026-11-23 支付GMV为什么下降",
    "as_of": "2026-11-23T23:59:59+08:00",
    "principal": {
        "principal_id": "container-smoke",
        "allowed_values": {
            "region": ["east", "west", "north", "south"],
            "channel": ["app", "web", "mini_program"],
            "category": ["electronics", "beauty", "home", "fashion"],
        },
    },
    "budget": {"max_queries": 4},
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test a running MetricLedger container using only stdlib"
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="running MetricLedger API base URL",
    )
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    health = _get_json(f"{base_url}/api/health")
    assert health["status"] == "ok"
    assert health["snapshots"] == 2
    assert health["ledger"] is True

    index_html = _get_text(f"{base_url}/")
    assert "<title>MetricLedger · 可信指标分析</title>" in index_html
    assert '<div id="root"></div>' in index_html

    catalog = _get_json(f"{base_url}/api/catalog")
    metric_ids = {metric["metric_id"] for metric in catalog["metrics"]}
    assert metric_ids == {
        "paid_gmv",
        "paid_conversion_rate",
        "cancel_rate",
    }

    evaluation = _get_json(f"{base_url}/api/eval/latest")
    assert evaluation["status"] == "COMPLETE"
    assert evaluation["scenario_count"] >= 72

    analysis = _post_json(f"{base_url}/api/analyses", ANALYSIS_REQUEST)
    assert analysis["admission"]["status"] == "ADMITTED"
    assert analysis["completeness"] == "COMPLETE"
    assert len(analysis["claims"]) >= 2
    assert all(claim["evidence_refs"] for claim in analysis["claims"])
    assert analysis["budget_usage"]["queries_used"] == 4
    assert analysis["budget_usage"]["planner_usage_complete"] is True
    assert analysis["budget_usage"]["planner_tokens_used"] >= 0
    assert analysis["investigation"]["rounds"]
    assert len(analysis["sql_audit"]) == 4
    assert all(
        audit["sql"].lstrip().upper().startswith("SELECT")
        for audit in analysis["sql_audit"]
    )

    rendered = "\n".join(
        claim["rendered_text"] for claim in analysis["claims"]
    )
    assert "732,400.00" in rendered
    assert "-9.18%" in rendered
    assert "{evidence:" not in rendered

    metrics = _get_text(f"{base_url}/api/metrics")
    assert "metricledger_http_requests_total" in metrics
    assert "metricledger_duckdb_queries_total" in metrics
    assert "metricledger_planner_calls_total" in metrics
    assert "metricledger_planner_tokens_total" in metrics
    assert "metricledger_investigation_steps_total" in metrics

    print(
        "container smoke: ok "
        f"(claims={len(analysis['claims'])}, "
        f"queries={analysis['budget_usage']['queries_used']}, "
        f"scenarios={evaluation['scenario_count']})"
    )


def _get_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_text(url: str) -> str:
    request = Request(url)
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()
