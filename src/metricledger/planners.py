from __future__ import annotations

import json
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
    ) -> list[Hypothesis]:
        if remaining_queries <= 0:
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
        try:
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
            return self._sanitize(
                raw_hypotheses,
                observation,
                remaining_queries=remaining_queries,
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._fallback.propose(
                observation,
                allowed_dimensions,
                remaining_queries=remaining_queries,
            )
        finally:
            if owns_client:
                client.close()

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
