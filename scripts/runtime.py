from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from metricledger.data import DuckDbEngine
from metricledger.ledger import SqliteLedger
from metricledger.models import AnalysisRequest, BudgetPolicy, Principal
from metricledger.planners import OpenAICompatibleHypothesisPlanner
from metricledger.semantic import SemanticRegistry
from metricledger.service import AnalysisService
from metricledger.snapshots import SnapshotRepository
from scripts.build_demo_data import DEMO_SNAPSHOTS, build_demo_database


DEFAULT_QUESTION = "2026-11-17 到 2026-11-23 支付GMV为什么下降"
DEFAULT_AS_OF = "2026-11-23T23:59:59+08:00"
DEMO_ALLOWED_VALUES = {
    "region": ["east", "west", "north", "south"],
    "channel": ["app", "web", "mini_program"],
    "category": ["electronics", "beauty", "home", "fashion"],
}


def _planner_from_environment() -> OpenAICompatibleHypothesisPlanner | None:
    base_url = os.environ.get("METRICLEDGER_PLANNER_BASE_URL", "").strip()
    model = os.environ.get("METRICLEDGER_PLANNER_MODEL", "").strip()
    api_key = os.environ.get("METRICLEDGER_PLANNER_API_KEY", "").strip() or None
    has_endpoint = bool(base_url)
    has_model = bool(model)

    if has_endpoint != has_model:
        raise ValueError(
            "METRICLEDGER_PLANNER_BASE_URL and METRICLEDGER_PLANNER_MODEL "
            "must be configured together"
        )
    if api_key is not None and not has_endpoint:
        raise ValueError(
            "METRICLEDGER_PLANNER_API_KEY requires "
            "METRICLEDGER_PLANNER_BASE_URL and METRICLEDGER_PLANNER_MODEL"
        )
    if not has_endpoint:
        return None

    return OpenAICompatibleHypothesisPlanner(
        base_url=base_url,
        model=model,
        api_key=api_key,
    )


def build_service(
    *,
    database_path: str | Path = "data/metricledger.duckdb",
    ledger_path: str | Path | None = "data/audit.sqlite3",
) -> AnalysisService:
    """Build the same analysis service used by the API and the local CLI."""

    database = Path(database_path)
    if not database.exists():
        build_demo_database(database)

    ledger = SqliteLedger(Path(ledger_path)) if ledger_path is not None else None
    return AnalysisService(
        registry=SemanticRegistry.load_default(),
        snapshots=SnapshotRepository.from_records(DEMO_SNAPSHOTS),
        engine=DuckDbEngine(
            database,
            {
                snapshot["snapshot_id"]: snapshot["table_name"]
                for snapshot in DEMO_SNAPSHOTS
            },
        ),
        ledger=ledger,
        planner=_planner_from_environment(),
    )


def demo_request(
    question: str = DEFAULT_QUESTION,
    *,
    as_of: str = DEFAULT_AS_OF,
) -> AnalysisRequest:
    return AnalysisRequest(
        question=question,
        as_of=datetime.fromisoformat(as_of),
        principal=Principal(
            principal_id="local-cli",
            allowed_values=DEMO_ALLOWED_VALUES,
        ),
        budget=BudgetPolicy(max_queries=4),
    )
