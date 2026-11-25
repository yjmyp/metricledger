from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from metricledger.api import create_app
from scripts.runtime import build_service


def main() -> None:
    database_path = Path(os.environ.get("METRICLEDGER_DB", "data/metricledger.duckdb"))
    ledger_path = Path(os.environ.get("METRICLEDGER_LEDGER", "data/audit.sqlite3"))
    service = build_service(
        database_path=database_path,
        ledger_path=ledger_path,
    )
    uvicorn.run(
        create_app(service),
        host=os.environ.get("METRICLEDGER_HOST", "127.0.0.1"),
        port=int(os.environ.get("METRICLEDGER_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
