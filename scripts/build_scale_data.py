from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_demo_data import DEMO_SNAPSHOTS


def build_scale_database(
    path: str | Path,
    *,
    rows_per_snapshot: int,
    demo_share: float = 0.9,
) -> Path:
    """Build deterministic event-level snapshots for reproducible load tests."""

    if rows_per_snapshot < 1:
        raise ValueError("rows_per_snapshot must be positive")
    if not 0 < demo_share < 1:
        raise ValueError("demo_share must be between zero and one")

    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    demo_rows = int(rows_per_snapshot * demo_share)
    with duckdb.connect(str(database_path)) as connection:
        for snapshot, end_date in zip(
            DEMO_SNAPSHOTS,
            (date(2026, 11, 16), date(2026, 11, 23)),
            strict=True,
        ):
            _create_event_table(
                connection,
                table=snapshot["table_name"],
                end_date=end_date,
                rows=rows_per_snapshot,
                demo_rows=demo_rows,
            )
    return database_path


def _create_event_table(
    connection: duckdb.DuckDBPyConnection,
    *,
    table: str,
    end_date: date,
    rows: int,
    demo_rows: int,
) -> None:
    day_count = (end_date - date(2026, 10, 1)).days + 1
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE {table} AS
        SELECT
            CASE WHEN row_id < ? THEN 'demo' ELSE 'tenant_b' END AS tenant_id,
            DATE '2026-10-01' + CAST(row_id % ? AS INTEGER) AS event_date,
            (['east', 'west', 'north', 'south'])[1 + CAST(row_id % 4 AS INTEGER)]
                AS region,
            (['app', 'web', 'mini_program'])[1 + CAST((row_id // 4) % 3 AS INTEGER)]
                AS channel,
            (['electronics', 'beauty', 'home', 'fashion'])[
                1 + CAST((row_id // 12) % 4 AS INTEGER)
            ] AS category,
            CAST(1 + (row_id % 5) AS INTEGER) AS paid_orders,
            CAST(5 + (row_id % 13) AS INTEGER) AS sessions,
            CAST(CASE WHEN row_id % 11 = 0 THEN 1 ELSE 0 END AS INTEGER)
                AS canceled_orders,
            CAST(10.0 + CAST(row_id % 1000 AS DOUBLE) / 10.0 AS DOUBLE)
                AS paid_gmv
        FROM range(?) AS events(row_id)
        """,
        [demo_rows, day_count, rows],
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build deterministic million-event snapshots"
    )
    parser.add_argument(
        "--database",
        default="data/metricledger-scale.duckdb",
        help="DuckDB output path",
    )
    parser.add_argument(
        "--rows-per-snapshot",
        type=int,
        default=1_000_000,
    )
    parser.add_argument("--demo-share", type=float, default=0.9)
    args = parser.parse_args()

    path = build_scale_database(
        args.database,
        rows_per_snapshot=args.rows_per_snapshot,
        demo_share=args.demo_share,
    )
    size_mb = path.stat().st_size / (1024 * 1024)
    print(
        f"built {path}: 2 snapshots, "
        f"{args.rows_per_snapshot * 2:,} events, {size_mb:.2f} MiB"
    )


if __name__ == "__main__":
    main()
