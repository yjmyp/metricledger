from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from metricledger.api import create_app
from metricledger.data import DuckDbEngine
from metricledger.ledger import SqliteLedger
from metricledger.service import AnalysisService
from metricledger.semantic import SemanticRegistry
from metricledger.snapshots import SnapshotRepository
from scripts.build_demo_data import DEMO_SNAPSHOTS, build_demo_database


def main() -> None:
    database_path = Path(os.environ.get("METRICLEDGER_DB", "data/metricledger.duckdb"))
    ledger_path = Path(os.environ.get("METRICLEDGER_LEDGER", "data/audit.sqlite3"))
    if not database_path.exists():
        build_demo_database(database_path)

    registry = SemanticRegistry.load_default()
    service = AnalysisService(
        registry=registry,
        snapshots=SnapshotRepository.from_records(DEMO_SNAPSHOTS),
        engine=DuckDbEngine(
            database_path,
            {
                snapshot["snapshot_id"]: snapshot["table_name"]
                for snapshot in DEMO_SNAPSHOTS
            },
        ),
        ledger=SqliteLedger(ledger_path),
    )
    uvicorn.run(
        create_app(service),
        host=os.environ.get("METRICLEDGER_HOST", "127.0.0.1"),
        port=int(os.environ.get("METRICLEDGER_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
