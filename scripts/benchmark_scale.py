from __future__ import annotations

import argparse
import json
import math
import platform
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from metricledger.data import DuckDbEngine
from metricledger.semantic import SemanticRegistry
from metricledger.service import AnalysisService
from metricledger.snapshots import SnapshotRepository
from scripts.build_demo_data import DEMO_SNAPSHOTS
from scripts.runtime import demo_request


def run_scale_benchmark(
    database: str | Path,
    *,
    output_json: str | Path,
    output_markdown: str | Path,
) -> dict:
    """Run the real investigation service against a scale database."""

    database_path = Path(database).resolve()
    if not database_path.is_file():
        raise FileNotFoundError(
            f"scale database does not exist: {database_path}"
        )

    registry = SemanticRegistry.load_default()
    snapshots = SnapshotRepository.from_records(DEMO_SNAPSHOTS)
    rows_by_snapshot: dict[str, int] = {}
    tenant_counts: dict[str, int] = {}
    date_bounds: dict[str, dict[str, str]] = {}

    with duckdb.connect(str(database_path), read_only=True) as connection:
        for snapshot in DEMO_SNAPSHOTS:
            table = _quoted_identifier(snapshot["table_name"])
            snapshot_id = snapshot["snapshot_id"]
            rows_by_snapshot[snapshot_id] = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            )
            for tenant_id, count in connection.execute(
                f"""
                SELECT tenant_id, COUNT(*)
                FROM {table}
                GROUP BY tenant_id
                ORDER BY tenant_id
                """
            ).fetchall():
                tenant_counts[str(tenant_id)] = (
                    tenant_counts.get(str(tenant_id), 0) + int(count)
                )
            minimum, maximum = connection.execute(
                f"SELECT MIN(event_date), MAX(event_date) FROM {table}"
            ).fetchone()
            date_bounds[snapshot_id] = {
                "minimum": minimum.isoformat(),
                "maximum": maximum.isoformat(),
            }
        database_size = _duckdb_database_size(connection)

    engine = DuckDbEngine(
        database_path,
        {
            snapshot["snapshot_id"]: snapshot["table_name"]
            for snapshot in DEMO_SNAPSHOTS
        },
    )
    service = AnalysisService(
        registry=registry,
        snapshots=snapshots,
        engine=engine,
    )
    wall_started = time.perf_counter()
    response = service.analyze(demo_request())
    wall_elapsed_ms = (time.perf_counter() - wall_started) * 1000

    explain_evidence = []
    seen_fingerprints: set[str] = set()
    with duckdb.connect(str(database_path), read_only=True) as connection:
        for audit in response.sql_audit:
            if audit.fingerprint in seen_fingerprints:
                continue
            seen_fingerprints.add(audit.fingerprint)
            plan_rows = connection.execute(
                "EXPLAIN ANALYZE " + audit.sql,
                audit.parameters,
            ).fetchall()
            plan_text = "\n".join(
                str(value)
                for row in plan_rows
                for value in row
                if value is not None
            )
            explain_evidence.append(
                {
                    "result_id": audit.result_id,
                    "sql_fingerprint": audit.fingerprint,
                    "row_count": audit.row_count,
                    "engine_elapsed_ms": audit.elapsed_ms,
                    "total_time_seconds": _plan_total_seconds(plan_text),
                    "plan": plan_text,
                }
            )

    total_events = sum(rows_by_snapshot.values())
    report = {
        "status": "COMPLETE",
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "dataset": {
            "kind": "deterministic synthetic event-level snapshots",
            "path": _display_path(database_path),
            "total_events": total_events,
        },
        "database": {
            "total_events": total_events,
            "file_size_bytes": database_path.stat().st_size,
            "rows_by_snapshot": rows_by_snapshot,
            "tenant_event_counts": tenant_counts,
            "date_bounds_by_snapshot": date_bounds,
            "database_size_pragma": database_size,
        },
        "analysis": {
            "analysis_id": response.analysis_id,
            "admission_status": response.admission.status.value,
            "completeness": response.completeness.value,
            "queries_used": response.budget_usage.queries_used,
            "claim_count": len(response.claims),
            "answer": response.answer,
            "evidence_rows": [artifact.row_count for artifact in response.evidence],
            "sql_audit": [
                {
                    "result_id": audit.result_id,
                    "sql": audit.sql,
                    "parameters": audit.parameters,
                    "fingerprint": audit.fingerprint,
                    "row_count": audit.row_count,
                    "snapshot_id": audit.snapshot_id,
                    "elapsed_ms": audit.elapsed_ms,
                }
                for audit in response.sql_audit
            ],
            "service_wall_elapsed_ms": round(wall_elapsed_ms, 3),
        },
        "database_stats": {
            "execution_count": engine.execution_count,
            "error_count": engine.query_error_count,
            "total_query_ms": round(engine.total_query_ms, 3),
            "average_query_ms": round(
                engine.total_query_ms / engine.execution_count,
                3,
            )
            if engine.execution_count
            else 0.0,
        },
        "explain_analyze": explain_evidence,
        "environment": {
            "python": platform.python_version(),
            "duckdb": duckdb.__version__,
            "platform": platform.platform(),
        },
        "caveat": (
            "This is a local measurement on deterministic synthetic data, "
            "not a production SLA or an external benchmark."
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


def _duckdb_database_size(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, str | int]:
    cursor = connection.execute("PRAGMA database_size")
    names = [description[0] for description in cursor.description]
    values = cursor.fetchone()
    return {
        name: value if isinstance(value, (str, int)) else str(value)
        for name, value in zip(names, values, strict=True)
    }


def _plan_total_seconds(plan: str) -> float:
    match = re.search(r"Total Time:\s*([0-9.]+)s", plan)
    if match is None:
        return 0.0
    value = float(match.group(1))
    return round(value, 6) if math.isfinite(value) else 0.0


def _render_markdown(report: dict) -> str:
    rows = report["database"]["rows_by_snapshot"]
    tenants = report["database"]["tenant_event_counts"]
    stats = report["database_stats"]
    analysis = report["analysis"]
    lines = [
        "# MetricLedger Scale Benchmark",
        "",
        "> 本报告通过真实 DuckDB 执行参数化只读查询。数据是由仓库脚本生成的事件级快照，不是生产流量。",
        f"> 生成时间：`{report['generated_at']}`",
        "",
        "## 真实 DuckDB 查询规模",
        "",
        f"- 数据库：`{report['dataset']['path']}`",
        f"- 文件大小：**{report['database']['file_size_bytes'] / (1024 * 1024):.2f} MiB**",
        f"- 事件总数：**{report['database']['total_events']:,}**",
        f"- 快照表：`{len(rows)}`",
        "",
        "| 快照 | 事件数 | 日期下限 | 日期上限 |",
        "|---|---:|---|---|",
    ]
    for snapshot_id, count in rows.items():
        bounds = report["database"]["date_bounds_by_snapshot"][snapshot_id]
        lines.append(
            f"| `{snapshot_id}` | {count:,} | "
            f"`{bounds['minimum']}` | `{bounds['maximum']}` |"
        )
    lines.extend(
        [
            "",
            "租户事件分布："
            + "，".join(
                f"`{tenant}`={count:,}" for tenant, count in tenants.items()
            ),
            "",
            "## 实际分析请求",
            "",
            "| 指标 | 实测值 |",
            "|---|---:|",
            f"| 准入状态 | `{analysis['admission_status']}` |",
            f"| 完整度 | `{analysis['completeness']}` |",
            f"| Claim 数 | {analysis['claim_count']} |",
            f"| SQL 查询数 | {analysis['queries_used']} |",
            f"| DuckDB 总查询耗时 | {stats['total_query_ms']:.3f} ms |",
            f"| DuckDB 平均查询耗时 | {stats['average_query_ms']:.3f} ms |",
            f"| 服务端到端耗时 | {analysis['service_wall_elapsed_ms']:.3f} ms |",
            "",
            "实际答案：",
            "",
            "```text",
            analysis["answer"] or "<no answer>",
            "```",
            "",
            "## SQL 审计与查询计划",
            "",
            "| 结果 | 行数 | 引擎耗时 | EXPLAIN ANALYZE 总耗时 |",
            "|---|---:|---:|---:|",
        ]
    )
    explain_by_result = {
        item["result_id"]: item for item in report["explain_analyze"]
    }
    for audit in analysis["sql_audit"]:
        explain = explain_by_result.get(audit["result_id"], {})
        total_seconds = explain.get("total_time_seconds", 0.0)
        lines.append(
            f"| `{audit['result_id']}` | {audit['row_count']} | "
            f"{audit['elapsed_ms']} ms | {total_seconds * 1000:.3f} ms |"
        )

    for explain in report["explain_analyze"]:
        lines.extend(
            [
                "",
                f"### `{explain['result_id']}`",
                "",
                "```text",
                explain["plan"],
                "```",
            ]
        )

    lines.extend(
        [
            "",
            "## 口径与限制",
            "",
            "- `execution_count` 来自同一个 `DuckDbEngine` 实例，只统计 `AnalysisService` 实际执行次数。",
            "- `EXPLAIN ANALYZE` 会再次执行查询，因此不计入上面的服务查询次数。",
            "- 数据规模可复现，但耗时受本机 CPU、内存、DuckDB 版本和文件缓存影响。",
            "- 本报告不包含并发吞吐结论；并发结论见 load 报告。",
            "",
        ]
    )
    return "\n".join(lines)


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark MetricLedger against a DuckDB scale dataset"
    )
    parser.add_argument(
        "--database",
        default="data/metricledger-scale.duckdb",
    )
    parser.add_argument(
        "--output-json",
        default="docs/verification/scale-results.json",
    )
    parser.add_argument(
        "--output-markdown",
        default="docs/verification/scale-report.md",
    )
    args = parser.parse_args()

    report = run_scale_benchmark(
        args.database,
        output_json=args.output_json,
        output_markdown=args.output_markdown,
    )
    print(
        "scale: "
        f"events={report['database']['total_events']:,}, "
        f"queries={report['analysis']['queries_used']}, "
        f"db_ms={report['database_stats']['total_query_ms']:.3f}"
    )
    print(f"json: {args.output_json}")
    print(f"markdown: {args.output_markdown}")


if __name__ == "__main__":
    main()
