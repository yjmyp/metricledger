from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from metricledger.api import create_app
from metricledger.observability import RequestMetrics
from scripts.runtime import (
    authenticator_from_environment,
    build_service,
    rate_limiter_from_environment,
)


def main() -> None:
    database_path = Path(os.environ.get("METRICLEDGER_DB", "data/metricledger.duckdb"))
    ledger_path = Path(os.environ.get("METRICLEDGER_LEDGER", "data/audit.sqlite3"))
    service = build_service(
        database_path=database_path,
        ledger_path=ledger_path,
    )
    static_dir = Path(
        os.environ.get("METRICLEDGER_STATIC_DIR", "web/dist")
    )
    configured_eval_path = os.environ.get(
        "METRICLEDGER_EVAL_PATH",
        "",
    ).strip()
    uvicorn.run(
        create_app(
            service,
            authenticator=authenticator_from_environment(),
            rate_limiter=rate_limiter_from_environment(),
            metrics=RequestMetrics(),
            static_dir=static_dir if static_dir.is_dir() else None,
            eval_path=configured_eval_path or None,
        ),
        host=os.environ.get("METRICLEDGER_HOST", "127.0.0.1"),
        port=int(os.environ.get("METRICLEDGER_PORT", "8000")),
        log_level=os.environ.get("METRICLEDGER_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
