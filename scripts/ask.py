from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.runtime import DEFAULT_AS_OF, DEFAULT_QUESTION, build_service, demo_request


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Ask MetricLedger directly without a browser or API server"
    )
    parser.add_argument(
        "question",
        nargs="?",
        default=DEFAULT_QUESTION,
        help=f"business question (default: {DEFAULT_QUESTION})",
    )
    parser.add_argument(
        "--as-of",
        default=DEFAULT_AS_OF,
        help="timezone-aware analysis cutoff",
    )
    parser.add_argument(
        "--database-path",
        default=os.environ.get("METRICLEDGER_DB", "data/metricledger.duckdb"),
        help="DuckDB snapshot database",
    )
    parser.add_argument(
        "--ledger-path",
        default=os.environ.get("METRICLEDGER_LEDGER", "data/audit.sqlite3"),
        help="SQLite audit ledger",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the full structured response",
    )
    args = parser.parse_args()

    service = build_service(
        database_path=args.database_path,
        ledger_path=args.ledger_path,
    )
    response = service.analyze(demo_request(args.question, as_of=args.as_of))

    if args.json:
        print(
            json.dumps(
                response.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    evidence_refs = sum(
        len(validated.claim.evidence_refs) for validated in response.claims
    )
    max_queries = (
        response.context.budget.max_queries if response.context is not None else 0
    )
    all_select = all(
        audit.sql.lstrip().upper().startswith("SELECT")
        for audit in response.sql_audit
    )
    print("MetricLedger | 进程内 CLI（无需浏览器或 HTTP 服务）")
    print(f"问题: {args.question}")
    print(f"准入: {response.admission.status.value}")
    print(f"完整度: {response.completeness.value}")
    print("")
    print(response.answer or "未生成答案")
    print("")
    print(
        f"Claim: {len(response.claims)} | "
        f"证据产物: {len(response.evidence)} | "
        f"证据引用: {evidence_refs} | "
        f"查询: {response.budget_usage.queries_used}/{max_queries} | "
        f"SQL 全为 SELECT: {str(all_select).lower()}"
    )
    if response.issues:
        print("提示: " + "; ".join(issue.code for issue in response.issues))


if __name__ == "__main__":
    main()
