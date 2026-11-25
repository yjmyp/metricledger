from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from .models import DimensionDefinition, Hypothesis, ObservationSummary


class HypothesisPlanner(Protocol):
    def propose(
        self,
        observation: ObservationSummary,
        allowed_dimensions: list[DimensionDefinition],
        *,
        remaining_queries: int,
    ) -> list[Hypothesis]:
        """Return a bounded, semantically valid diagnostic order."""


class HeuristicHypothesisPlanner:
    def propose(
        self,
        observation: ObservationSummary,
        allowed_dimensions: list[DimensionDefinition],
        *,
        remaining_queries: int,
    ) -> list[Hypothesis]:
        candidate_ids = set(observation.candidate_dimensions)
        limit = min(3, max(0, remaining_queries))
        hypotheses: list[Hypothesis] = []
        for dimension in allowed_dimensions:
            if dimension.dimension_id not in candidate_ids:
                continue
            hypotheses.append(
                Hypothesis(
                    dimension_id=dimension.dimension_id,
                    rationale_code="semantic_default",
                    priority=min(len(hypotheses), 2),
                )
            )
            if len(hypotheses) == limit:
                break
        return hypotheses


@dataclass(frozen=True)
class PlannerTrace:
    outcome: str
    fallback_used: bool
    fallback_reason: str | None
    latency_ms: float
    model_called: bool


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
        self.last_trace = PlannerTrace(
            outcome="NOT_RUN",
            fallback_used=False,
            fallback_reason=None,
            latency_ms=0.0,
            model_called=False,
        )

    def propose(
        self,
        observation: ObservationSummary,
        allowed_dimensions: list[DimensionDefinition],
        *,
        remaining_queries: int,
    ) -> list[Hypothesis]:
        if remaining_queries <= 0:
            self.last_trace = PlannerTrace(
                outcome="NOT_RUN",
                fallback_used=False,
                fallback_reason="no remaining queries",
                latency_ms=0.0,
                model_called=False,
            )
            return []
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
                                for dimension in allowed_dimensions
                                if dimension.dimension_id
                                in observation.candidate_dimensions
                            ],
                            "remaining_queries": remaining_queries,
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
        model_called = False
        try:
            model_called = True
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(_strip_code_fence(content))
            raw_hypotheses = parsed["hypotheses"]
            if not isinstance(raw_hypotheses, list):
                raise ValueError("hypotheses must be a list")
            hypotheses = self._sanitize(
                raw_hypotheses,
                observation,
                remaining_queries=remaining_queries,
            )
            if hypotheses:
                self.last_trace = PlannerTrace(
                    outcome="MODEL_SUCCESS",
                    fallback_used=False,
                    fallback_reason=None,
                    latency_ms=_elapsed_ms(started),
                    model_called=True,
                )
                return hypotheses
            return self._record_fallback(
                outcome="FALLBACK_INVALID_OUTPUT",
                reason="no valid hypotheses",
                observation=observation,
                allowed_dimensions=allowed_dimensions,
                remaining_queries=remaining_queries,
                started=started,
                model_called=model_called,
            )
        except httpx.TimeoutException as exc:
            return self._record_fallback(
                outcome="FALLBACK_TIMEOUT",
                reason=f"timeout: {type(exc).__name__}",
                observation=observation,
                allowed_dimensions=allowed_dimensions,
                remaining_queries=remaining_queries,
                started=started,
                model_called=model_called,
            )
        except httpx.HTTPStatusError as exc:
            return self._record_fallback(
                outcome="FALLBACK_HTTP_ERROR",
                reason=f"http status {exc.response.status_code}",
                observation=observation,
                allowed_dimensions=allowed_dimensions,
                remaining_queries=remaining_queries,
                started=started,
                model_called=model_called,
            )
        except httpx.HTTPError as exc:
            return self._record_fallback(
                outcome="FALLBACK_HTTP_ERROR",
                reason=f"http error: {type(exc).__name__}",
                observation=observation,
                allowed_dimensions=allowed_dimensions,
                remaining_queries=remaining_queries,
                started=started,
                model_called=model_called,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._record_fallback(
                outcome="FALLBACK_INVALID_OUTPUT",
                reason="invalid model response",
                observation=observation,
                allowed_dimensions=allowed_dimensions,
                remaining_queries=remaining_queries,
                started=started,
                model_called=model_called,
            )
        finally:
            if owns_client:
                client.close()

    def _record_fallback(
        self,
        *,
        outcome: str,
        reason: str,
        observation: ObservationSummary,
        allowed_dimensions: list[DimensionDefinition],
        remaining_queries: int,
        started: float,
        model_called: bool,
    ) -> list[Hypothesis]:
        hypotheses = self._fallback.propose(
                observation,
                allowed_dimensions,
                remaining_queries=remaining_queries,
            )
        self.last_trace = PlannerTrace(
            outcome=outcome,
            fallback_used=True,
            fallback_reason=reason,
            latency_ms=_elapsed_ms(started),
            model_called=model_called,
        )
        return hypotheses

    @staticmethod
    def _sanitize(
        raw_hypotheses: list[object],
        observation: ObservationSummary,
        *,
        remaining_queries: int,
    ) -> list[Hypothesis]:
        allowed = set(observation.candidate_dimensions)
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
            if len(hypotheses) == min(3, remaining_queries):
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
