from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import duckdb


REGIONS = ("east", "west", "north", "south")
CHANNELS = ("app", "web", "mini_program")
CATEGORIES = ("electronics", "beauty", "home", "fashion")

DEMO_SNAPSHOTS = [
    {
        "snapshot_id": "batch_20261116_2300",
        "table_name": "sales_batch_20261116_2300",
        "reporting_as_of": "2026-11-16T23:59:59+08:00",
        "period_start": "2026-10-01",
        "period_end": "2026-11-16",
        "complete": True,
        "duplicate_rate": 0.0,
        "null_rate": 0.0,
        "manifest_hash": "manifest-1116",
    },
    {
        "snapshot_id": "batch_20261123_2300",
        "table_name": "sales_batch_20261123_2300",
        "reporting_as_of": "2026-11-23T23:59:59+08:00",
        "period_start": "2026-10-01",
        "period_end": "2026-11-23",
        "complete": True,
        "duplicate_rate": 0.0,
        "null_rate": 0.0,
        "manifest_hash": "manifest-1123",
    },
]


def build_demo_database(path: str | Path) -> Path:
    """Create two deterministic snapshot tables at the requested path."""

    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(database_path)) as connection:
        _create_table(
            connection,
            "sales_batch_20261116_2300",
            end_date=date(2026, 11, 16),
        )
        _create_table(
            connection,
            "sales_batch_20261123_2300",
            end_date=date(2026, 11, 23),
        )
    return database_path


def _create_table(connection: duckdb.DuckDBPyConnection, table: str, end_date: date) -> None:
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE {table} (
            event_date DATE NOT NULL,
            region VARCHAR NOT NULL,
            channel VARCHAR NOT NULL,
            category VARCHAR NOT NULL,
            paid_orders INTEGER NOT NULL,
            sessions INTEGER NOT NULL,
            canceled_orders INTEGER NOT NULL,
            paid_gmv DOUBLE NOT NULL
        )
        """
    )
    connection.execute(
        f"""
        INSERT INTO {table}
        WITH dates AS (
            SELECT range::DATE AS event_date
            FROM range(
                DATE '2026-10-01',
                DATE '{end_date.isoformat()}'::DATE + INTERVAL 1 DAY,
                INTERVAL 1 DAY
            )
        ),
        regions(region, region_index) AS (
            VALUES ('east', 0), ('west', 1), ('north', 2), ('south', 3)
        ),
        channels(channel, channel_index) AS (
            VALUES ('app', 0), ('web', 1), ('mini_program', 2)
        ),
        categories(category, category_index) AS (
            VALUES ('electronics', 0), ('beauty', 1), ('home', 2), ('fashion', 3)
        ),
        combinations AS (
            SELECT
                dates.event_date,
                regions.region,
                channels.channel,
                categories.category,
                regions.region_index,
                channels.channel_index,
                categories.category_index,
                (
                    dates.event_date BETWEEN DATE '2026-11-17' AND DATE '2026-11-23'
                    AND regions.region = 'east'
                ) AS east_incident
            FROM dates
            CROSS JOIN regions
            CROSS JOIN channels
            CROSS JOIN categories
        )
        SELECT
            event_date,
            region,
            channel,
            category,
            CASE WHEN east_incident THEN 12 ELSE 20 END AS paid_orders,
            100 + region_index + channel_index + category_index AS sessions,
            CASE WHEN east_incident THEN 3 ELSE 2 END AS canceled_orders,
            CASE
                WHEN east_incident
                    AND event_date = DATE '2026-11-17'
                    AND channel = 'app'
                    AND category = 'electronics'
                THEN 1440.0
                WHEN east_incident THEN 1520.0
                ELSE 2400.0
            END AS paid_gmv
        FROM combinations
        """
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build deterministic MetricLedger data")
    parser.add_argument(
        "--database",
        default="data/metricledger.duckdb",
        help="DuckDB output path",
    )
    args = parser.parse_args()
    path = build_demo_database(args.database)
    print(f"built {path} with {len(DEMO_SNAPSHOTS)} snapshots")


if __name__ == "__main__":
    main()
