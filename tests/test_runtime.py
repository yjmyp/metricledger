from __future__ import annotations

import json
from pathlib import Path

import pytest

from metricledger.planners import (
    HeuristicHypothesisPlanner,
    OpenAICompatibleHypothesisPlanner,
)
from scripts.runtime import (
    authenticator_from_environment,
    build_service,
    rate_limiter_from_environment,
)


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


def test_authenticator_from_environment_uses_hashed_bearer_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "METRICLEDGER_AUTH_JSON",
        json.dumps(
            {
                "runtime-secret": {
                    "tenant_id": "tenant_a",
                    "principal_id": "runtime-user",
                    "roles": ["viewer", "analyst"],
                    "allowed_values": {"region": ["east"]},
                }
            }
        ),
    )

    authenticator = authenticator_from_environment()

    assert authenticator is not None
    identity = authenticator.authenticate("Bearer runtime-secret")
    assert identity is not None
    assert identity.tenant_id == "tenant_a"
    assert identity.principal_id == "runtime-user"
    assert "runtime-secret" not in repr(authenticator.__dict__)


def test_authenticator_from_environment_rejects_malformed_json_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "METRICLEDGER_AUTH_JSON",
        '{"leaked-token": invalid-json',
    )

    with pytest.raises(ValueError) as error:
        authenticator_from_environment()

    assert "METRICLEDGER_AUTH_JSON must be a JSON object" in str(error.value)
    assert "leaked-token" not in str(error.value)


def test_rate_limiter_configuration_is_optional_and_enforces_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("METRICLEDGER_RATE_LIMIT_PER_MINUTE", raising=False)
    assert rate_limiter_from_environment() is None

    monkeypatch.setenv("METRICLEDGER_RATE_LIMIT_PER_MINUTE", "1")
    limiter = rate_limiter_from_environment()
    assert limiter is not None
    assert limiter.check("tenant:user").allowed is True
    assert limiter.check("tenant:user").allowed is False
