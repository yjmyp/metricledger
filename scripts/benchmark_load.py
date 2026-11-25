from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(PROJECT_ROOT))

from scripts.runtime import demo_request


EXPECTED_SEMANTIC_ISSUE_CODES: dict[str, frozenset[str]] = {
    "clarify": frozenset({"MISSING_PERIOD"}),
}


def percentile(values: list[float], quantile: float) -> float:
    """Return the nearest-rank percentile without interpolation."""

    if not values:
        return 0.0
    if not 0 < quantile <= 1:
        raise ValueError("quantile must be in (0, 1]")
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return round(float(ordered[index]), 3)


def infer_bottleneck(
    *,
    request_latencies_ms: list[float],
    route_latencies_ms: dict[str, list[float]],
    duckdb: dict[str, int | float],
    sqlite: dict[str, int | float],
) -> dict[str, Any]:
    """Infer DB pressure only from measured counters and request latency."""

    total_request_ms = sum(max(0.0, value) for value in request_latencies_ms)
    if not request_latencies_ms or total_request_ms <= 0:
        return _inconclusive_bottleneck(
            "No request latency measurements were collected."
        )
    if int(duckdb.get("query_count", 0)) <= 0:
        return _inconclusive_bottleneck(
            "No DuckDB execution counter was collected."
        )

    duckdb_time = max(0.0, float(duckdb.get("query_time_ms", 0.0)))
    sqlite_time = max(0.0, float(sqlite.get("write_time_ms", 0.0))) + max(
        0.0, float(sqlite.get("read_time_ms", 0.0))
    )
    duckdb_ratio = round(duckdb_time / total_request_ms, 6)
    sqlite_ratio = round(sqlite_time / total_request_ms, 6)
    candidates = {
        "duckdb": duckdb_ratio,
        "sqlite": sqlite_ratio,
    }
    primary = max(candidates, key=candidates.get)
    primary_ratio = candidates[primary]
    route_p95 = {
        route: percentile(latencies, 0.95)
        for route, latencies in route_latencies_ms.items()
        if latencies
    }
    slowest_route = max(route_p95, key=route_p95.get) if route_p95 else None
    return {
        "status": "MEASURED",
        "primary_candidate": primary if primary_ratio > 0 else None,
        "primary_candidate_ratio": primary_ratio,
        "duckdb_time_to_request_time_ratio": duckdb_ratio,
        "sqlite_time_to_request_time_ratio": sqlite_ratio,
        "slowest_route_by_p95": slowest_route,
        "slowest_route_p95_ms": route_p95.get(slowest_route, 0.0)
        if slowest_route
        else 0.0,
        "duckdb_error_count": int(duckdb.get("error_count", 0)),
        "sqlite_error_count": int(sqlite.get("error_count", 0)),
        "evidence": (
            "DB time ratios use the sum of wall-clock request latencies "
            "as the denominator; this is a local attribution heuristic, "
            "not a profiler or production trace."
        ),
    }


def _inconclusive_bottleneck(reason: str) -> dict[str, Any]:
    return {
        "status": "INCONCLUSIVE",
        "primary_candidate": None,
        "primary_candidate_ratio": 0.0,
        "duckdb_time_to_request_time_ratio": 0.0,
        "sqlite_time_to_request_time_ratio": 0.0,
        "slowest_route_by_p95": None,
        "slowest_route_p95_ms": 0.0,
        "duckdb_error_count": 0,
        "sqlite_error_count": 0,
        "reason": reason,
    }


async def run_load_benchmark(
    base_url: str,
    *,
    requests: int = 120,
    concurrency: int = 12,
    output_json: str | Path = "docs/verification/load-results.json",
    output_markdown: str | Path = "docs/verification/load-report.md",
    bearer_token: str | None = None,
    timeout_seconds: float = 30.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Run a real mixed HTTP load against an already-running MetricLedger API."""

    if requests < 1:
        raise ValueError("requests must be positive")
    if concurrency < 1:
        raise ValueError("concurrency must be positive")

    base_url = base_url.rstrip("/")
    headers = _request_headers(bearer_token)
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=timeout_seconds,
        headers=headers,
        transport=transport,
    ) as client:
        analysis_id = await _warm_up_analysis(client)
        baseline_metrics_text = await _read_metrics(client)
        specs = _route_specs(analysis_id)
        schedule = _build_schedule(requests, specs)
        semaphore = asyncio.Semaphore(concurrency)
        started = time.perf_counter()

        async def run_one(route: str) -> dict[str, Any]:
            async with semaphore:
                request_started = time.perf_counter()
                status_code = 0
                error: str | None = None
                semantic_issue_codes: list[str] = []
                try:
                    response = await specs[route](client)
                    status_code = response.status_code
                    semantic_issue_codes = _semantic_issue_codes(response)
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                elapsed_ms = (time.perf_counter() - request_started) * 1000
                expected_codes = EXPECTED_SEMANTIC_ISSUE_CODES.get(
                    route,
                    frozenset(),
                )
                unexpected_codes = sorted(
                    set(semantic_issue_codes) - expected_codes
                )
                missing_expected_codes = sorted(
                    expected_codes - set(semantic_issue_codes)
                )
                return {
                    "route": route,
                    "status_code": status_code,
                    "elapsed_ms": round(elapsed_ms, 3),
                    "error": error,
                    "semantic_issue_codes": semantic_issue_codes,
                    "expected_semantic_issue_codes": sorted(expected_codes),
                    "unexpected_semantic_issue_codes": unexpected_codes,
                    "missing_expected_semantic_issue_codes": (
                        missing_expected_codes
                    ),
                }

        results = await asyncio.gather(*(run_one(route) for route in schedule))
        wall_seconds = max(0.0, time.perf_counter() - started)
        metrics_text = await _read_metrics(client)

    latencies = [item["elapsed_ms"] for item in results]
    route_latencies: dict[str, list[float]] = {}
    for item in results:
        route_latencies.setdefault(item["route"], []).append(item["elapsed_ms"])
    status_counts = Counter(str(item["status_code"]) for item in results)
    http_error_count = sum(
        1
        for item in results
        if item["error"] is not None or item["status_code"] >= 400
    )
    expected_clarification_count = sum(
        1
        for item in results
        if item["route"] == "clarify"
        and item["semantic_issue_codes"]
        == item["expected_semantic_issue_codes"]
        and item["status_code"] < 400
    )
    semantic_error_count = sum(
        1
        for item in results
        if item["unexpected_semantic_issue_codes"]
        or item["missing_expected_semantic_issue_codes"]
    )
    error_count = sum(
        1
        for item in results
        if item["error"] is not None
        or item["status_code"] >= 400
        or item["unexpected_semantic_issue_codes"]
        or item["missing_expected_semantic_issue_codes"]
    )
    db_counters = _database_counter_delta(
        _parse_db_counters(baseline_metrics_text),
        _parse_db_counters(metrics_text),
    )
    bottleneck = infer_bottleneck(
        request_latencies_ms=latencies,
        route_latencies_ms=route_latencies,
        duckdb=db_counters["duckdb"],
        sqlite=db_counters["sqlite"],
    )
    report = {
        "status": "COMPLETE",
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "environment": {
            "python": platform.python_version(),
            "httpx": httpx.__version__,
            "base_url": base_url,
            "concurrency": concurrency,
            "planned_requests": requests,
        },
        "workload": {
            "duration_seconds": round(wall_seconds, 3),
            "completed_requests": len(results),
            "throughput_requests_per_second": round(
                len(results) / wall_seconds, 3
            )
            if wall_seconds
            else 0.0,
            "http_error_count": http_error_count,
            "semantic_error_count": semantic_error_count,
            "expected_clarification_count": expected_clarification_count,
            "error_count": error_count,
            "error_rate": round(error_count / len(results), 6)
            if results
            else 0.0,
            "status_counts": dict(sorted(status_counts.items())),
            "routes": {
                route: {
                    "count": len(values),
                    "p50_ms": percentile(values, 0.50),
                    "p95_ms": percentile(values, 0.95),
                    "p99_ms": percentile(values, 0.99),
                    "max_ms": round(max(values), 3),
                }
                for route, values in sorted(route_latencies.items())
            },
        },
        "latency_ms": {
            "min": round(min(latencies), 3) if latencies else 0.0,
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": round(max(latencies), 3) if latencies else 0.0,
        },
        "database_counters": db_counters,
        "bottleneck": bottleneck,
        "raw_results": results,
        "caveat": (
            "This is a local mixed HTTP measurement against one API process "
            "and a local DuckDB/SQLite setup. It is not a production SLA."
        ),
    }

    output_json_path = Path(output_json)
    output_markdown_path = Path(output_markdown)
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    output_markdown_path.write_text(
        _render_markdown(report),
        encoding="utf-8",
    )
    return report


def _request_headers(bearer_token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    }
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    return headers


def _semantic_issue_codes(response: httpx.Response) -> list[str]:
    """Treat error-level issues in HTTP 200 analysis responses as failures."""

    if response.status_code >= 400:
        return []
    try:
        payload = response.json()
    except ValueError:
        return []
    issues = payload.get("issues") if isinstance(payload, dict) else None
    if not isinstance(issues, list):
        return []
    return sorted(
        {
            str(issue.get("code", "UNKNOWN_ANALYSIS_ERROR"))
            for issue in issues
            if isinstance(issue, dict)
            and str(issue.get("severity", "")).upper() == "ERROR"
        }
    )


async def _warm_up_analysis(client: httpx.AsyncClient) -> str | None:
    try:
        response = await client.post(
            "/api/analyses",
            json=demo_request().model_dump(mode="json"),
        )
        response.raise_for_status()
        payload = response.json()
        value = payload.get("analysis_id")
        return str(value) if value else None
    except Exception:
        return None


def _route_specs(
    analysis_id: str | None,
) -> dict[str, Callable[[httpx.AsyncClient], Awaitable[httpx.Response]]]:
    valid_payload = demo_request().model_dump(mode="json")
    value_payload = demo_request(
        "2026-11-17 到 2026-11-23 支付GMV是多少"
    ).model_dump(mode="json")
    clarification_payload = demo_request("支付GMV是多少").model_dump(mode="json")
    readback_id = analysis_id or "missing-analysis"

    return {
        "health": lambda client: client.get("/api/health"),
        "catalog": lambda client: client.get("/api/catalog"),
        "investigate": lambda client: client.post(
            "/api/analyses", json=valid_payload
        ),
        "value": lambda client: client.post(
            "/api/analyses", json=value_payload
        ),
        "clarify": lambda client: client.post(
            "/api/analyses", json=clarification_payload
        ),
        "readback": lambda client: client.get(
            f"/api/analyses/{readback_id}"
        ),
        "eval": lambda client: client.get("/api/eval/latest"),
    }


def _build_schedule(
    requests: int,
    specs: dict[str, Callable[..., Awaitable[httpx.Response]]],
) -> list[str]:
    weights = [
        ("health", 1),
        ("catalog", 1),
        ("investigate", 3),
        ("value", 2),
        ("clarify", 1),
        ("readback", 1),
        ("eval", 1),
    ]
    weighted = [
        route
        for route, weight in weights
        for _ in range(weight)
        if route in specs
    ]
    return [weighted[index % len(weighted)] for index in range(requests)]


async def _read_metrics(client: httpx.AsyncClient) -> str:
    try:
        response = await client.get("/api/metrics")
        response.raise_for_status()
        return response.text
    except Exception:
        return ""


def _parse_db_counters(payload: str) -> dict[str, dict[str, int | float]]:
    numbers: dict[str, float] = {}
    for line in payload.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            numbers[parts[0]] = float(parts[1])
        except ValueError:
            continue
    return {
        "duckdb": {
            "query_count": int(
                numbers.get("metricledger_duckdb_queries_total", 0)
            ),
            "query_time_ms": round(
                numbers.get(
                    "metricledger_duckdb_query_time_seconds_total",
                    0.0,
                )
                * 1000,
                3,
            ),
            "error_count": int(
                numbers.get("metricledger_duckdb_query_errors_total", 0)
            ),
        },
        "sqlite": {
            "write_count": int(
                numbers.get("metricledger_sqlite_ledger_writes_total", 0)
            ),
            "write_time_ms": round(
                numbers.get(
                    "metricledger_sqlite_ledger_write_time_seconds_total",
                    0.0,
                )
                * 1000,
                3,
            ),
            "read_count": int(
                numbers.get("metricledger_sqlite_ledger_reads_total", 0)
            ),
            "read_time_ms": round(
                numbers.get(
                    "metricledger_sqlite_ledger_read_time_seconds_total",
                    0.0,
                )
                * 1000,
                3,
            ),
            "error_count": int(
                numbers.get(
                    "metricledger_sqlite_ledger_write_errors_total",
                    0,
                )
                + numbers.get(
                    "metricledger_sqlite_ledger_read_errors_total",
                    0,
                )
            ),
        },
    }


def _database_counter_delta(
    baseline: dict[str, dict[str, int | float]],
    final: dict[str, dict[str, int | float]],
) -> dict[str, dict[str, int | float]]:
    """Report database work caused by the measured workload only."""

    result: dict[str, dict[str, int | float]] = {}
    for store, final_values in final.items():
        baseline_values = baseline.get(store, {})
        result[store] = {}
        for name, final_value in final_values.items():
            baseline_value = baseline_values.get(name, 0)
            delta = final_value - baseline_value
            if isinstance(final_value, int) and isinstance(baseline_value, int):
                result[store][name] = max(0, delta)
            else:
                result[store][name] = round(max(0.0, float(delta)), 3)
    return result


def _render_markdown(report: dict[str, Any]) -> str:
    workload = report["workload"]
    lines = [
        "# MetricLedger Mixed-Load Benchmark",
        "",
        f"> 生成时间：`{report['generated_at']}`",
        "> 本报告来自真实运行中的 HTTP API、真实 DuckDB 查询和 SQLite 账本；不是模拟请求。",
        "",
        "## 负载配置",
        "",
        f"- Base URL：`{report['environment']['base_url']}`",
        f"- 并发：**{report['environment']['concurrency']}**",
        f"- 完成请求：**{workload['completed_requests']}**",
        f"- 端到端持续时间：**{workload['duration_seconds']:.3f}s**",
        f"- 吞吐：**{workload['throughput_requests_per_second']:.3f} req/s**",
        f"- HTTP/传输错误数：**{workload['http_error_count']}**",
        f"- 预期澄清响应数：**{workload['expected_clarification_count']}**",
        f"- 非预期业务错误数：**{workload['semantic_error_count']}**",
        f"- 错误率：**{workload['error_rate']:.2%}**",
        "",
        "## 延迟",
        "",
        "| 指标 | 实测值 |",
        "|---|---:|",
        f"| P50 | {report['latency_ms']['p50']:.3f} ms |",
        f"| P95 | {report['latency_ms']['p95']:.3f} ms |",
        f"| P99 | {report['latency_ms']['p99']:.3f} ms |",
        f"| Max | {report['latency_ms']['max']:.3f} ms |",
        "",
        "## 各路由",
        "",
        "| 路由 | 请求数 | P50 | P95 | P99 | Max |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for route, metrics in workload["routes"].items():
        lines.append(
            f"| `{route}` | {metrics['count']} | "
            f"{metrics['p50_ms']:.3f} ms | {metrics['p95_ms']:.3f} ms | "
            f"{metrics['p99_ms']:.3f} ms | {metrics['max_ms']:.3f} ms |"
        )
    lines.extend(
        [
            "",
            "## 数据库证据",
            "",
            "| 存储 | 实测计数 |",
            "|---|---|",
            f"| DuckDB 查询数 | {report['database_counters']['duckdb']['query_count']} |",
            f"| DuckDB 查询耗时 | {report['database_counters']['duckdb']['query_time_ms']:.3f} ms |",
            f"| DuckDB 错误数 | {report['database_counters']['duckdb']['error_count']} |",
            f"| SQLite 写入数 | {report['database_counters']['sqlite']['write_count']} |",
            f"| SQLite 写入耗时 | {report['database_counters']['sqlite']['write_time_ms']:.3f} ms |",
            f"| SQLite 读取数 | {report['database_counters']['sqlite']['read_count']} |",
            f"| SQLite 读取耗时 | {report['database_counters']['sqlite']['read_time_ms']:.3f} ms |",
            f"| SQLite 错误数 | {report['database_counters']['sqlite']['error_count']} |",
            "",
            "## 瓶颈判断",
            "",
            f"- 状态：**{report['bottleneck']['status']}**",
            f"- 首要候选：`{report['bottleneck']['primary_candidate']}`",
            f"- DuckDB 查询耗时 / 请求总耗时：**{report['bottleneck']['duckdb_time_to_request_time_ratio']:.2%}**",
            f"- SQLite 账本耗时 / 请求总耗时：**{report['bottleneck']['sqlite_time_to_request_time_ratio']:.2%}**",
            f"- 最慢路由：`{report['bottleneck']['slowest_route_by_p95']}`，P95 {report['bottleneck']['slowest_route_p95_ms']:.3f} ms",
            "",
            "> 该比例是本地归因启发式：用累计请求延迟作分母，不是 profiler、分布式 trace 或线上容量结论。",
            "",
            "## 限制",
            "",
            report["caveat"],
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a real mixed-load benchmark against MetricLedger"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=120)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument(
        "--output-json",
        default="docs/verification/load-results.json",
    )
    parser.add_argument(
        "--output-markdown",
        default="docs/verification/load-report.md",
    )
    parser.add_argument("--bearer-token", default=os.environ.get("METRICLEDGER_TOKEN"))
    args = parser.parse_args()
    report = asyncio.run(
        run_load_benchmark(
            args.base_url,
            requests=args.requests,
            concurrency=args.concurrency,
            output_json=args.output_json,
            output_markdown=args.output_markdown,
            bearer_token=args.bearer_token,
        )
    )
    print(
        f"load: rps={report['workload']['throughput_requests_per_second']:.3f}, "
        f"p95={report['latency_ms']['p95']:.3f}ms, "
        f"error_rate={report['workload']['error_rate']:.2%}"
    )
    print(f"bottleneck: {report['bottleneck']}")
    print(f"json: {args.output_json}")
    print(f"markdown: {args.output_markdown}")


if __name__ == "__main__":
    main()
