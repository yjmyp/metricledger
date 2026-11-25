from __future__ import annotations

import argparse

import httpx


ANALYSIS_REQUEST = {
    "question": "2026-11-17 到 2026-11-23 支付GMV为什么下降",
    "as_of": "2026-11-23T23:59:59+08:00",
    "principal": {
        "principal_id": "smoke-test",
        "allowed_values": {
            "region": ["east", "west", "north", "south"],
            "channel": ["app", "web", "mini_program"],
            "category": ["electronics", "beauty", "home", "fashion"],
        },
    },
    "budget": {"max_queries": 4},
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a live MetricLedger smoke test")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="running API or Vite proxy base URL",
    )
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=30) as client:
        health = _json(client.get("/api/health"))
        assert health["status"] == "ok"
        assert health["snapshots"] == 2

        catalog = _json(client.get("/api/catalog"))
        metric_ids = {metric["metric_id"] for metric in catalog["metrics"]}
        assert metric_ids == {
            "paid_gmv",
            "paid_conversion_rate",
            "cancel_rate",
        }

        evaluation = _json(client.get("/api/eval/latest"))
        assert evaluation["status"] == "COMPLETE"
        assert evaluation["scenario_count"] >= 60

        analysis = _json(client.post("/api/analyses", json=ANALYSIS_REQUEST))
        assert analysis["admission"]["status"] == "ADMITTED"
        assert analysis["completeness"] in {"COMPLETE", "PARTIAL"}
        assert len(analysis["claims"]) >= 2
        assert all(claim["evidence_refs"] for claim in analysis["claims"])
        assert analysis["sql_audit"]
        assert analysis["sql_audit"][0]["sql"].lstrip().upper().startswith("SELECT")

        rendered = "\n".join(
            claim["rendered_text"] for claim in analysis["claims"]
        )
        assert "732,400.00" in rendered
        assert "-9.18%" in rendered
        assert "{evidence:" not in rendered

    print(
        "smoke: ok "
        f"(claims={len(analysis['claims'])}, "
        f"queries={analysis['budget_usage']['queries_used']}, "
        f"scenarios={evaluation['scenario_count']})"
    )


def _json(response: httpx.Response) -> dict:
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    main()
