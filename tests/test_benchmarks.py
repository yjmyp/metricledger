from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from scripts.benchmark_load import infer_bottleneck, percentile, run_load_benchmark
from scripts.benchmark_planners import run_planner_benchmark
from scripts.benchmark_scale import run_scale_benchmark
from scripts.build_scale_data import build_scale_database


def test_scale_benchmark_runs_real_queries_and_reports_exact_rows(tmp_path: Path):
    database = build_scale_database(
        tmp_path / "scale.duckdb",
        rows_per_snapshot=5_000,
        demo_share=0.9,
    )

    report = run_scale_benchmark(
        database,
        output_json=tmp_path / "scale.json",
        output_markdown=tmp_path / "scale.md",
    )

    assert report["status"] == "COMPLETE"
    assert report["database"]["total_events"] == 10_000
    assert report["database"]["rows_by_snapshot"] == {
        "batch_20261116_2300": 5_000,
        "batch_20261123_2300": 5_000,
    }
    assert report["analysis"]["queries_used"] >= 1
    assert report["database_stats"]["execution_count"] == report["analysis"][
        "queries_used"
    ]
    assert all(
        audit["sql"].lstrip().upper().startswith("SELECT")
        for audit in report["analysis"]["sql_audit"]
    )
    assert (tmp_path / "scale.json").is_file()
    assert "真实 DuckDB" in (tmp_path / "scale.md").read_text(encoding="utf-8")


def test_planner_benchmark_marks_model_not_run_without_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("METRICLEDGER_PLANNER_BASE_URL", raising=False)
    monkeypatch.delenv("METRICLEDGER_PLANNER_MODEL", raising=False)
    monkeypatch.delenv("METRICLEDGER_PLANNER_API_KEY", raising=False)

    report = run_planner_benchmark(
        output_json=tmp_path / "planner.json",
        output_markdown=tmp_path / "planner.md",
    )

    assert report["status"] == "COMPLETE"
    assert report["heuristic"]["case_count"] == 15
    assert report["model"]["status"] == "NOT_RUN"
    assert report["model"]["credentials_configured"] is False
    assert "NOT_RUN" in (tmp_path / "planner.md").read_text(encoding="utf-8")


def test_planner_benchmark_runs_configured_openai_compatible_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv(
        "METRICLEDGER_PLANNER_BASE_URL",
        "https://planner.example/v1",
    )
    monkeypatch.setenv("METRICLEDGER_PLANNER_MODEL", "test-model")
    monkeypatch.delenv("METRICLEDGER_PLANNER_API_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        allowed = json.loads(payload["messages"][1]["content"])["dimensions"]
        hypotheses = [
            {
                "dimension_id": dimension["dimension_id"],
                "rationale_code": "model_choice",
                "priority": min(index, 2),
            }
            for index, dimension in enumerate(allowed[:1])
        ]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"hypotheses": hypotheses})
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            },
        )

    report = run_planner_benchmark(
        output_json=tmp_path / "planner-model.json",
        output_markdown=tmp_path / "planner-model.md",
        model_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert report["model"]["status"] == "RUN"
    assert report["model"]["case_count"] == 15
    assert report["model"]["fallback_rate"] == 0.0
    assert report["model"]["failure_rate"] == 0.0
    assert report["model"]["usage_complete_count"] == 15
    assert report["model"]["planner_tokens"] == 180


def test_percentile_uses_nearest_rank_without_interpolation():
    assert percentile([1, 2, 3, 4, 100], 0.95) == 100
    assert percentile([], 0.95) == 0.0


def test_bottleneck_inference_uses_measured_database_time():
    result = infer_bottleneck(
        request_latencies_ms=[100.0, 100.0, 100.0, 100.0],
        route_latencies_ms={
            "health": [5.0, 5.0],
            "investigate": [390.0],
        },
        duckdb={"query_count": 4, "query_time_ms": 320.0, "error_count": 0},
        sqlite={"write_count": 1, "write_time_ms": 4.0, "error_count": 0},
    )

    assert result["status"] == "MEASURED"
    assert result["primary_candidate"] == "duckdb"
    assert result["duckdb_time_to_request_time_ratio"] == pytest.approx(0.8)


def test_bottleneck_inference_is_inconclusive_without_database_measurements():
    result = infer_bottleneck(
        request_latencies_ms=[10.0],
        route_latencies_ms={"health": [10.0]},
        duckdb={"query_count": 0, "query_time_ms": 0.0, "error_count": 0},
        sqlite={"write_count": 0, "write_time_ms": 0.0, "error_count": 0},
    )

    assert result["status"] == "INCONCLUSIVE"
    assert result["primary_candidate"] is None


def test_load_benchmark_writes_completed_request_count(
    tmp_path: Path,
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/metrics":
            return httpx.Response(
                200,
                text=(
                    "metricledger_duckdb_queries_total 7\n"
                    "metricledger_duckdb_query_time_seconds_total 0.14\n"
                    "metricledger_duckdb_query_errors_total 0\n"
                ),
            )
        if request.method == "POST" and request.url.path == "/api/analyses":
            return httpx.Response(200, json={"analysis_id": "analysis-1"})
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(handler)
    report = asyncio.run(
        run_load_benchmark(
            "http://benchmark.test",
            requests=7,
            concurrency=2,
            output_json=tmp_path / "load.json",
            output_markdown=tmp_path / "load.md",
            transport=transport,
        )
    )

    assert report["workload"]["completed_requests"] == 7
    markdown = (tmp_path / "load.md").read_text(encoding="utf-8")
    assert "完成请求：**7**" in markdown


def test_load_benchmark_counts_semantic_failures_in_http_200_responses(
    tmp_path: Path,
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/metrics":
            return httpx.Response(200, text="")
        if request.method == "POST" and request.url.path == "/api/analyses":
            return httpx.Response(
                200,
                json={
                    "analysis_id": "analysis-1",
                    "issues": [
                        {
                            "code": "HEADLINE_QUERY_FAILED",
                            "severity": "ERROR",
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"status": "ok"})

    report = asyncio.run(
        run_load_benchmark(
            "http://benchmark.test",
            requests=3,
            concurrency=1,
            output_json=tmp_path / "load.json",
            output_markdown=tmp_path / "load.md",
            transport=httpx.MockTransport(handler),
        )
    )

    assert report["workload"]["semantic_error_count"] == 1
    assert report["workload"]["error_count"] == 1
    assert report["workload"]["error_rate"] == pytest.approx(1 / 3)


def test_load_benchmark_separates_expected_clarification_from_failures(
    tmp_path: Path,
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/metrics":
            return httpx.Response(200, text="")
        if request.method == "POST" and request.url.path == "/api/analyses":
            payload = json.loads(request.content)
            issues = (
                [{"code": "MISSING_PERIOD", "severity": "ERROR"}]
                if payload["question"] == "支付GMV是多少"
                else []
            )
            return httpx.Response(
                200,
                json={"analysis_id": "analysis-1", "issues": issues},
            )
        return httpx.Response(200, json={"status": "ok"})

    report = asyncio.run(
        run_load_benchmark(
            "http://benchmark.test",
            requests=10,
            concurrency=1,
            output_json=tmp_path / "load.json",
            output_markdown=tmp_path / "load.md",
            transport=httpx.MockTransport(handler),
        )
    )

    assert report["workload"]["expected_clarification_count"] == 1
    assert report["workload"]["semantic_error_count"] == 0
    assert report["workload"]["error_count"] == 0
    assert report["workload"]["error_rate"] == 0


def test_load_benchmark_reports_database_counter_deltas_after_warmup(
    tmp_path: Path,
):
    duckdb_queries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal duckdb_queries
        if request.url.path == "/api/metrics":
            return httpx.Response(
                200,
                text=(
                    "metricledger_duckdb_queries_total "
                    f"{duckdb_queries}\n"
                    "metricledger_duckdb_query_time_seconds_total "
                    f"{duckdb_queries / 100:.6f}\n"
                    "metricledger_duckdb_query_errors_total 0\n"
                ),
            )
        if request.method == "POST" and request.url.path == "/api/analyses":
            duckdb_queries += 4
            return httpx.Response(200, json={"analysis_id": "analysis-1"})
        return httpx.Response(200, json={"status": "ok"})

    report = asyncio.run(
        run_load_benchmark(
            "http://benchmark.test",
            requests=3,
            concurrency=1,
            output_json=tmp_path / "load.json",
            output_markdown=tmp_path / "load.md",
            transport=httpx.MockTransport(handler),
        )
    )

    assert report["database_counters"]["duckdb"]["query_count"] == 4
    assert report["database_counters"]["duckdb"]["query_time_ms"] == 40.0
