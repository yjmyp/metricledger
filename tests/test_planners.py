import json

import httpx
import pytest

from metricledger.models import (
    EvidenceObservation,
    EvidenceStatus,
    MetricKind,
    ModelUsage,
    ObservationSummary,
    QuestionKind,
)
from metricledger.planners import (
    HeuristicHypothesisPlanner,
    OpenAICompatibleHypothesisPlanner,
)
from metricledger.semantic import SemanticRegistry


def observation(round_number=1, completed=None):
    return ObservationSummary(
        analysis_id="analysis-1",
        metric_id="paid_gmv",
        metric_kind=MetricKind.ADDITIVE,
        question_kind=QuestionKind.INVESTIGATE,
        candidate_dimensions=["region", "channel", "category"],
        completed_dimensions=completed or [],
        round_number=round_number,
        remaining_queries=4,
        remaining_planner_tokens=1000,
        evidence_summaries=[
            EvidenceObservation(
                dimension_id="region",
                status=EvidenceStatus.MATERIAL_CHANGE,
                result_ids=["r1", "r2"],
                material_change=10.0,
                concentration_share=0.3,
                group_count=4,
                top_group="east",
            )
        ],
    )


def test_heuristic_planner_returns_decision_and_zero_usage():
    registry = SemanticRegistry.load_default()

    decision = HeuristicHypothesisPlanner().propose(
        observation(),
        list(registry.bundle.dimensions),
        remaining_queries=4,
        round_number=1,
    )

    assert [item.dimension_id for item in decision.hypotheses] == [
        "region",
        "channel",
    ]
    assert decision.usage is not None
    assert decision.usage.total_tokens == 0
    assert decision.attempt.outcome == "HEURISTIC"
    assert decision.attempt.model_called is False


def test_http_planner_filters_completed_unknown_and_duplicate_dimensions():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        supplied = json.loads(body["messages"][1]["content"])
        assert [
            dimension["dimension_id"]
            for dimension in supplied["dimensions"]
        ] == ["channel", "category"]
        content = json.dumps(
            {
                "hypotheses": [
                    {
                        "dimension_id": "region",
                        "rationale_code": "done",
                        "priority": 0,
                    },
                    {
                        "dimension_id": "channel",
                        "rationale_code": "ok",
                        "priority": 0,
                    },
                    {
                        "dimension_id": "channel",
                        "rationale_code": "dup",
                        "priority": 1,
                    },
                    {
                        "dimension_id": "invented",
                        "rationale_code": "bad",
                        "priority": 2,
                    },
                    {
                        "dimension_id": "category",
                        "rationale_code": "ok",
                        "priority": 2,
                    },
                ]
            }
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
            },
        )

    planner = OpenAICompatibleHypothesisPlanner(
        base_url="https://planner.example/v1",
        model="test-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    registry = SemanticRegistry.load_default()

    decision = planner.propose(
        observation(round_number=2, completed=["region"]),
        list(registry.bundle.dimensions),
        remaining_queries=4,
        round_number=2,
    )

    assert [item.dimension_id for item in decision.hypotheses] == [
        "channel",
        "category",
    ]
    assert decision.usage == ModelUsage(
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
    )
    assert decision.attempt.outcome == "MODEL_SUCCESS"
    assert decision.attempt.usage_complete is True


def test_http_planner_marks_missing_usage_incomplete():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "hypotheses": [
                                        {
                                            "dimension_id": "region",
                                            "rationale_code": "ok",
                                            "priority": 0,
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )

    planner = OpenAICompatibleHypothesisPlanner(
        base_url="https://planner.example/v1",
        model="test-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    decision = planner.propose(
        observation(),
        list(SemanticRegistry.load_default().bundle.dimensions),
        remaining_queries=4,
        round_number=1,
    )

    assert decision.usage is None
    assert decision.attempt.usage_complete is False


def test_http_planner_falls_back_when_all_model_choices_are_invalid():
    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(
            {
                "hypotheses": [
                    {
                        "dimension_id": "invented_dimension",
                        "rationale_code": "unsafe",
                        "priority": 0,
                    }
                ]
            }
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            },
        )

    planner = OpenAICompatibleHypothesisPlanner(
        base_url="https://planner.example/v1",
        model="test-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    decision = planner.propose(
        observation(),
        list(SemanticRegistry.load_default().bundle.dimensions),
        remaining_queries=4,
        round_number=1,
    )

    assert [item.dimension_id for item in decision.hypotheses] == [
        "region",
        "channel",
    ]
    assert decision.attempt.outcome == "FALLBACK_INVALID_OUTPUT"
    assert decision.attempt.fallback_used is True
    assert decision.attempt.fallback_reason == "no valid hypotheses"
    assert decision.usage is not None
    assert decision.usage.total_tokens == 12


@pytest.mark.parametrize(
    ("transport_error", "expected"),
    [
        (
            httpx.ReadTimeout("timed out"),
            "FALLBACK_TIMEOUT",
        ),
        (
            httpx.ConnectError("offline"),
            "FALLBACK_HTTP_ERROR",
        ),
    ],
)
def test_http_planner_records_fallback_reason(transport_error, expected):
    def handler(request: httpx.Request) -> httpx.Response:
        raise transport_error

    planner = OpenAICompatibleHypothesisPlanner(
        base_url="https://planner.example/v1",
        model="test-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    decision = planner.propose(
        observation(),
        list(SemanticRegistry.load_default().bundle.dimensions),
        remaining_queries=4,
        round_number=1,
    )

    assert decision.hypotheses
    assert decision.attempt.outcome == expected
    assert decision.attempt.fallback_used is True
    assert decision.attempt.usage_complete is False
    assert decision.usage is None


def test_http_planner_falls_back_on_invalid_json_without_claiming_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "not-json"}}]},
        )

    planner = OpenAICompatibleHypothesisPlanner(
        base_url="https://planner.example/v1",
        model="test-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    decision = planner.propose(
        observation(),
        list(SemanticRegistry.load_default().bundle.dimensions),
        remaining_queries=4,
        round_number=1,
    )

    assert decision.hypotheses
    assert decision.attempt.outcome == "FALLBACK_INVALID_OUTPUT"
    assert decision.usage is None
    assert decision.attempt.usage_complete is False


def test_http_planner_does_not_call_model_without_query_pair_budget():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("model must not be called")

    planner = OpenAICompatibleHypothesisPlanner(
        base_url="https://planner.example/v1",
        model="test-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    decision = planner.propose(
        observation(),
        list(SemanticRegistry.load_default().bundle.dimensions),
        remaining_queries=1,
        round_number=1,
    )

    assert calls == 0
    assert decision.hypotheses == []
    assert decision.attempt.outcome == "NOT_RUN"
    assert decision.attempt.model_called is False
