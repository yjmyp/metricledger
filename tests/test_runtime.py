from __future__ import annotations

from pathlib import Path

import pytest

from metricledger.planners import (
    HeuristicHypothesisPlanner,
    OpenAICompatibleHypothesisPlanner,
)
from scripts.runtime import build_service


def test_build_service_uses_heuristic_planner_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("METRICLEDGER_PLANNER_BASE_URL", raising=False)
    monkeypatch.delenv("METRICLEDGER_PLANNER_MODEL", raising=False)
    monkeypatch.delenv("METRICLEDGER_PLANNER_API_KEY", raising=False)

    service = build_service(
        database_path=tmp_path / "default.duckdb",
        ledger_path=None,
    )

    assert isinstance(service.planner, HeuristicHypothesisPlanner)


def test_build_service_configures_openai_planner_from_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "METRICLEDGER_PLANNER_BASE_URL",
        "https://planner.example/v1/",
    )
    monkeypatch.setenv("METRICLEDGER_PLANNER_MODEL", "test-model")
    monkeypatch.setenv("METRICLEDGER_PLANNER_API_KEY", "environment-secret")

    service = build_service(
        database_path=tmp_path / "configured.duckdb",
        ledger_path=None,
    )

    assert isinstance(service.planner, OpenAICompatibleHypothesisPlanner)
    assert service.planner.base_url == "https://planner.example/v1"
    assert service.planner.model == "test-model"
    assert service.planner.api_key == "environment-secret"


def test_build_service_rejects_partial_openai_planner_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "METRICLEDGER_PLANNER_BASE_URL",
        "https://planner.example/v1",
    )
    monkeypatch.delenv("METRICLEDGER_PLANNER_MODEL", raising=False)
    monkeypatch.delenv("METRICLEDGER_PLANNER_API_KEY", raising=False)

    with pytest.raises(
        ValueError,
        match="METRICLEDGER_PLANNER_BASE_URL and METRICLEDGER_PLANNER_MODEL",
    ):
        build_service(
            database_path=tmp_path / "partial.duckdb",
            ledger_path=None,
        )
