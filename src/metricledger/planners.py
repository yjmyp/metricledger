from __future__ import annotations

import json
import time
from typing import Protocol

import httpx

from .models import (
    DimensionDefinition,
    Hypothesis,
    ModelUsage,
    ObservationSummary,
    PlannerAttemptTrace,
    PlannerDecision,
)


class HypothesisPlanner(Protocol):
    def propose(
        self,
        observation: ObservationSummary,
        allowed_dimensions: list[DimensionDefinition],
        *,
        remaining_queries: int,
        round_number: int,
    ) -> PlannerDecision:
        """Return a bounded diagnostic order with an immutable trace."""


class HeuristicHypothesisPlanner:
    def propose(
        self,
        observation: ObservationSummary,
        allowed_dimensions: list[DimensionDefinition],
        *,
        remaining_queries: int,
        round_number: int,
    ) -> PlannerDecision:
        candidates = set(observation.candidate_dimensions)
        completed = set(observation.completed_dimensions)
        limit = min(3, max(0, remaining_queries // 2))
        hypotheses = [
            Hypothesis(
                dimension_id=dimension.dimension_id,
                rationale_code="semantic_default",
                priority=min(index, 2),
            )
            for index, dimension in enumerate(
                dimension
                for dimension in allowed_dimensions
                if dimension.dimension_id in candidates
                and dimension.dimension_id not in completed
            )
        ][:limit]
        return PlannerDecision(
            hypotheses=hypotheses,
            attempt=PlannerAttemptTrace(
                round_number=round_number,
                outcome="HEURISTIC",
                model_called=False,
                fallback_used=False,
                usage_complete=True,
                selected_dimensions=[
                    hypothesis.dimension_id for hypothesis in hypotheses
                ],
            ),
            usage=ModelUsage(),
        )


class OpenAICompatibleHypothesisPlanner:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        timeout_ms: int = 10_000,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.client = client
        self.timeout_ms = timeout_ms
        self._fallback = HeuristicHypothesisPlanner()

    def propose(
        self,
        observation: ObservationSummary,
        allowed_dimensions: list[DimensionDefinition],
        *,
        remaining_queries: int,
        round_number: int,
    ) -> PlannerDecision:
        available_dimensions = self._available_dimensions(
            observation,
            allowed_dimensions,
        )
        if remaining_queries < 2:
            return self._not_run_decision(
                round_number=round_number,
                reason="fewer than two remaining queries",
            )
        if not available_dimensions:
            return self._not_run_decision(
                round_number=round_number,
                reason="no remaining dimensions",
            )

        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Choose diagnostic dimensions for a metric investigation. "
                        "Return JSON only: {\"hypotheses\":[{\"dimension_id\":"
                        "\"...\",\"rationale_code\":\"...\",\"priority\":0}]}. "
                        "Use only supplied dimension IDs. Never invent values."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "observation": observation.model_dump(mode="json"),
                            "dimensions": [
                                {
                                    "dimension_id": dimension.dimension_id,
                                    "display_name": dimension.display_name,
                                }
                                for dimension in available_dimensions
                            ],
                            "remaining_queries": remaining_queries,
                            "round_number": round_number,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        owns_client = self.client is None
        client = self.client or httpx.Client(timeout=self.timeout_ms / 1000)
        started = time.perf_counter()
        usage: ModelUsage | None = None
        try:
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
            usage_payload = body.get("usage")
            usage = (
                ModelUsage.model_validate(usage_payload)
                if usage_payload is not None
                else None
            )
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(_strip_code_fence(content))
            raw_hypotheses = parsed["hypotheses"]
            if not isinstance(raw_hypotheses, list):
                raise ValueError("hypotheses must be a list")
            hypotheses = self.sanitize(
                raw_hypotheses,
                observation,
                available_dimensions,
                remaining_queries=remaining_queries,
            )
            if hypotheses:
                return PlannerDecision(
                    hypotheses=hypotheses,
                    attempt=PlannerAttemptTrace(
                        round_number=round_number,
                        outcome="MODEL_SUCCESS",
                        model_called=True,
                        latency_ms=_elapsed_ms(started),
                        usage_complete=usage is not None,
                        selected_dimensions=[
                            hypothesis.dimension_id
                            for hypothesis in hypotheses
                        ],
                    ),
                    usage=usage,
                )
            return self._fallback_decision(
                outcome="FALLBACK_INVALID_OUTPUT",
                reason="no valid hypotheses",
                observation=observation,
                allowed_dimensions=available_dimensions,
                remaining_queries=remaining_queries,
                round_number=round_number,
                started=started,
                usage=usage,
            )
        except httpx.TimeoutException as exc:
            return self._fallback_decision(
                outcome="FALLBACK_TIMEOUT",
                reason=f"timeout: {type(exc).__name__}",
                observation=observation,
                allowed_dimensions=available_dimensions,
                remaining_queries=remaining_queries,
                round_number=round_number,
                started=started,
                usage=usage,
            )
        except httpx.HTTPStatusError as exc:
            return self._fallback_decision(
                outcome="FALLBACK_HTTP_ERROR",
                reason=f"http status {exc.response.status_code}",
                observation=observation,
                allowed_dimensions=available_dimensions,
                remaining_queries=remaining_queries,
                round_number=round_number,
                started=started,
                usage=usage,
            )
        except httpx.HTTPError as exc:
            return self._fallback_decision(
                outcome="FALLBACK_HTTP_ERROR",
                reason=f"http error: {type(exc).__name__}",
                observation=observation,
                allowed_dimensions=available_dimensions,
                remaining_queries=remaining_queries,
                round_number=round_number,
                started=started,
                usage=usage,
            )
        except (KeyError, TypeError, ValueError):
            return self._fallback_decision(
                outcome="FALLBACK_INVALID_OUTPUT",
                reason="invalid model response",
                observation=observation,
                allowed_dimensions=available_dimensions,
                remaining_queries=remaining_queries,
                round_number=round_number,
                started=started,
                usage=usage,
            )
        finally:
            if owns_client:
                client.close()

    def _fallback_decision(
        self,
        *,
        outcome: str,
        reason: str,
        observation: ObservationSummary,
        allowed_dimensions: list[DimensionDefinition],
        remaining_queries: int,
        round_number: int,
        started: float,
        usage: ModelUsage | None,
    ) -> PlannerDecision:
        decision = self._fallback.propose(
            observation,
            allowed_dimensions,
            remaining_queries=remaining_queries,
            round_number=round_number,
        )
        attempt = decision.attempt.model_copy(
            update={
                "outcome": outcome,
                "model_called": True,
                "fallback_used": True,
                "fallback_reason": reason,
                "latency_ms": _elapsed_ms(started),
                "usage_complete": usage is not None,
            }
        )
        return decision.model_copy(
            update={
                "attempt": attempt,
                "usage": usage,
            }
        )

    @staticmethod
    def _not_run_decision(
        *,
        round_number: int,
        reason: str,
    ) -> PlannerDecision:
        return PlannerDecision(
            hypotheses=[],
            attempt=PlannerAttemptTrace(
                round_number=round_number,
                outcome="NOT_RUN",
                model_called=False,
                fallback_reason=reason,
                usage_complete=True,
                selected_dimensions=[],
            ),
            usage=ModelUsage(),
        )

    @staticmethod
    def _available_dimensions(
        observation: ObservationSummary,
        allowed_dimensions: list[DimensionDefinition],
    ) -> list[DimensionDefinition]:
        candidates = set(observation.candidate_dimensions)
        completed = set(observation.completed_dimensions)
        return [
            dimension
            for dimension in allowed_dimensions
            if dimension.dimension_id in candidates
            and dimension.dimension_id not in completed
        ]

    @staticmethod
    def sanitize(
        raw_hypotheses: list[object],
        observation: ObservationSummary,
        allowed_dimensions: list[DimensionDefinition],
        *,
        remaining_queries: int,
    ) -> list[Hypothesis]:
        allowed = {
            dimension.dimension_id
            for dimension in OpenAICompatibleHypothesisPlanner._available_dimensions(
                observation,
                allowed_dimensions,
            )
        }
        limit = min(3, max(0, remaining_queries // 2))
        seen: set[str] = set()
        hypotheses: list[Hypothesis] = []
        for raw in raw_hypotheses:
            if not isinstance(raw, dict):
                continue
            try:
                hypothesis = Hypothesis.model_validate(raw)
            except ValueError:
                continue
            if (
                hypothesis.dimension_id not in allowed
                or hypothesis.dimension_id in seen
            ):
                continue
            seen.add(hypothesis.dimension_id)
            hypotheses.append(hypothesis)
            if len(hypotheses) == limit:
                break
        return hypotheses


def _strip_code_fence(content: str) -> str:
    stripped = content.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _elapsed_ms(started: float) -> float:
    return round(max(0.0, (time.perf_counter() - started) * 1000), 3)
