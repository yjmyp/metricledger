from __future__ import annotations

import json
from importlib.metadata import requires
from pathlib import Path

import pytest

from metricledger.models import (
    AdmissionDecision,
    AdmissionStatus,
    AnalysisResponse,
    BudgetUsage,
    Completeness,
    EvidenceStatus,
    InvestigationObservation,
    InvestigationRoundTrace,
    InvestigationStepTrace,
    InvestigationTrace,
    MetricKind,
    PlannerAttemptTrace,
    QuestionKind,
)
from metricledger.observability import RequestMetrics
from metricledger.planners import (
    HeuristicHypothesisPlanner,
    OpenAICompatibleHypothesisPlanner,
)
from scripts.runtime import (
    authenticator_from_environment,
    build_service,
    rate_limiter_from_environment,
)


def test_distribution_declares_httpx_as_runtime_dependency() -> None:
    runtime_requirements = [
        requirement.split(";", 1)[0].strip()
        for requirement in requires("metricledger") or []
        if "extra ==" not in requirement
    ]

    assert any(
        requirement.lower().startswith("httpx")
        for requirement in runtime_requirements
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


def _investigation_response(
    *,
    planner_tokens_used: int | None,
    planner_usage_complete: bool,
) -> AnalysisResponse:
    def observation(
        round_number: int,
        completed_dimensions: list[str],
    ) -> InvestigationObservation:
        return InvestigationObservation(
            analysis_id="analysis-metrics",
            round_number=round_number,
            metric_id="paid_gmv",
            metric_kind=MetricKind.ADDITIVE,
            question_kind=QuestionKind.INVESTIGATE,
            candidate_dimensions=["region", "channel"],
            completed_dimensions=completed_dimensions,
            remaining_queries=4 - round_number * 2,
            remaining_planner_tokens=1880,
        )

    return AnalysisResponse(
        analysis_id="analysis-metrics",
        admission=AdmissionDecision(status=AdmissionStatus.ADMITTED),
        completeness=Completeness.NONE,
        answer=None,
        budget_usage=BudgetUsage(
            queries_used=4,
            planner_tokens_used=planner_tokens_used,
            planner_usage_complete=planner_usage_complete,
            elapsed_ms=88,
        ),
        investigation=InvestigationTrace(
            rounds=[
                InvestigationRoundTrace(
                    round_number=1,
                    observation=observation(1, []),
                    planner=PlannerAttemptTrace(
                        round_number=1,
                        outcome="MODEL_SUCCESS",
                        model_called=True,
                        latency_ms=42,
                    ),
                    steps=[
                        InvestigationStepTrace(
                            round_number=1,
                            dimension_id="region",
                            status=EvidenceStatus.NO_MATERIAL_CHANGE,
                            result_ids=["r1", "r2"],
                        )
                    ],
                ),
                InvestigationRoundTrace(
                    round_number=2,
                    observation=observation(2, ["region"]),
                    planner=PlannerAttemptTrace(
                        round_number=2,
                        outcome="FALLBACK_TIMEOUT",
                        model_called=True,
                        fallback_used=True,
                        fallback_reason="timeout",
                        latency_ms=10000,
                        usage_complete=False,
                    ),
                    steps=[
                        InvestigationStepTrace(
                            round_number=2,
                            dimension_id="channel",
                            status=EvidenceStatus.MATERIAL_CHANGE,
                            result_ids=["r3", "r4"],
                        )
                    ],
                ),
            ],
            completed_dimensions=["region", "channel"],
            stop_reason="context_drift",
            planner_fallback_used=True,
        ),
    )


def test_metrics_count_planner_steps_tokens_and_context_drift() -> None:
    metrics = RequestMetrics()

    metrics.record_analysis(
        _investigation_response(
            planner_tokens_used=120,
            planner_usage_complete=True,
        )
    )
    text = metrics.render()

    assert (
        'metricledger_planner_calls_total{outcome="MODEL_SUCCESS"} 1'
        in text
    )
    assert (
        'metricledger_planner_calls_total{outcome="FALLBACK_TIMEOUT"} 1'
        in text
    )
    assert "metricledger_planner_tokens_total 120" in text
    assert (
        "metricledger_investigation_steps_total"
        '{status="NO_MATERIAL_CHANGE"} 1'
        in text
    )
    assert (
        "metricledger_investigation_steps_total"
        '{status="MATERIAL_CHANGE"} 1'
        in text
    )
    assert "metricledger_context_drift_total 1" in text


def test_incomplete_planner_usage_does_not_increase_token_counter() -> None:
    metrics = RequestMetrics()

    metrics.record_analysis(
        _investigation_response(
            planner_tokens_used=None,
            planner_usage_complete=False,
        )
    )
    text = metrics.render()

    assert "metricledger_planner_tokens_total 0" in text
