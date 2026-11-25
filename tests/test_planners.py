import json

import httpx
import pytest

from metricledger.models import (
    MetricKind,
    ObservationSummary,
    QuestionKind,
)
from metricledger.planners import (
    HeuristicHypothesisPlanner,
    OpenAICompatibleHypothesisPlanner,
)
from metricledger.semantic import SemanticRegistry


def observation() -> ObservationSummary:
    return ObservationSummary(
        analysis_id="analysis-1",
        metric_id="paid_gmv",
        metric_kind=MetricKind.ADDITIVE,
        question_kind=QuestionKind.INVESTIGATE,
        candidate_dimensions=["region", "channel", "category"],
    )


def test_heuristic_planner_preserves_semantic_dimension_order():
    registry = SemanticRegistry.load_default()

    hypotheses = HeuristicHypothesisPlanner().propose(
        observation(),
        list(registry.bundle.dimensions),
        remaining_queries=4,
    )

    assert [hypothesis.dimension_id for hypothesis in hypotheses] == [
        "region",
        "channel",
        "category",
    ]


def test_http_planner_filters_unknown_and_duplicate_hypotheses():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "bounded rows" not in json.dumps(body, ensure_ascii=False)
        content = json.dumps(
            {
                "hypotheses": [
                    {
                        "dimension_id": "region",
                        "rationale_code": "semantic_default",
                        "priority": 0,
                    },
                    {
                        "dimension_id": "invented_dimension",
                        "rationale_code": "unsafe",
                        "priority": 0,
                    },
                    {
                        "dimension_id": "region",
                        "rationale_code": "duplicate",
                        "priority": 1,
                    },
                    {
                        "dimension_id": "channel",
                        "rationale_code": "region_contribution",
                        "priority": 2,
                    },
                ]
            }
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    planner = OpenAICompatibleHypothesisPlanner(
        base_url="https://planner.example/v1",
        model="test-model",
        client=client,
    )
    registry = SemanticRegistry.load_default()

    hypotheses = planner.propose(
        observation(),
        list(registry.bundle.dimensions),
        remaining_queries=4,
    )

    assert [hypothesis.dimension_id for hypothesis in hypotheses] == [
        "region",
        "channel",
    ]
    assert planner.last_trace.outcome == "MODEL_SUCCESS"
    assert planner.last_trace.fallback_used is False


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
            json={"choices": [{"message": {"content": content}}]},
        )

    planner = OpenAICompatibleHypothesisPlanner(
        base_url="https://planner.example/v1",
        model="test-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    registry = SemanticRegistry.load_default()

    hypotheses = planner.propose(
        observation(),
        list(registry.bundle.dimensions),
        remaining_queries=4,
    )

    assert [hypothesis.dimension_id for hypothesis in hypotheses] == [
        "region",
        "channel",
        "category",
    ]
    assert planner.last_trace.outcome == "FALLBACK_INVALID_OUTPUT"
    assert planner.last_trace.fallback_used is True
    assert planner.last_trace.fallback_reason == "no valid hypotheses"


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
    registry = SemanticRegistry.load_default()

    hypotheses = planner.propose(
        observation(),
        list(registry.bundle.dimensions),
        remaining_queries=4,
    )

    assert hypotheses
    assert planner.last_trace.outcome == expected
    assert planner.last_trace.fallback_used is True
