from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import duckdb

from scripts.build_scale_data import build_scale_database


def test_scale_builder_creates_tenant_partitioned_event_tables(tmp_path):
    database = build_scale_database(
        tmp_path / "scale.duckdb",
        rows_per_snapshot=2_000,
        demo_share=0.9,
    )

    with duckdb.connect(str(database), read_only=True) as connection:
        partitions = connection.execute(
            """
            SELECT tenant_id, count(*)
            FROM sales_batch_20261123_2300
            GROUP BY tenant_id
            ORDER BY tenant_id
            """
        ).fetchall()
        snapshots = connection.execute(
            """
            SELECT count(*)
            FROM (
                SELECT * FROM sales_batch_20261116_2300
                UNION ALL
                SELECT * FROM sales_batch_20261123_2300
            )
            """
        ).fetchone()[0]

    assert dict(partitions) == {"demo": 1_800, "tenant_b": 200}
    assert snapshots == 4_000


def test_scale_builder_cli_is_runnable_from_the_repository(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "cli-scale.duckdb"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_scale_data.py",
            "--database",
            str(database),
            "--rows-per-snapshot",
            "100",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert database.is_file()
