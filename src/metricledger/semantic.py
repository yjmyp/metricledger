from __future__ import annotations

import json
from importlib import resources
from typing import Any

from .models import DimensionDefinition, MetricDefinition, SemanticBundle, canonical_hash


class SemanticRegistry:
    def __init__(self, bundle: SemanticBundle):
        self.bundle = bundle
        self.metrics = {metric.metric_id: metric for metric in bundle.metrics}
        self.dimensions = {
            dimension.dimension_id: dimension for dimension in bundle.dimensions
        }
        self.freshness_policies = {
            policy.policy_id: policy for policy in bundle.freshness_policies
        }
        self.quality_policies = {
            policy.policy_id: policy for policy in bundle.quality_policies
        }

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> SemanticRegistry:
        payload = dict(data)
        payload.pop("canonical_hash", None)
        bundle = SemanticBundle.model_validate(payload)
        digest = canonical_hash(bundle.model_dump(mode="json"))
        return cls(bundle.model_copy(update={"canonical_hash": digest}))

    @classmethod
    def load_default(cls) -> SemanticRegistry:
        path = resources.files("metricledger").joinpath("resources/ecommerce_v1.json")
        return cls.from_mapping(json.loads(path.read_text(encoding="utf-8")))

    def metric(self, metric_id: str) -> MetricDefinition:
        try:
            return self.metrics[metric_id]
        except KeyError as exc:
            raise ValueError(f"unknown metric: {metric_id}") from exc

    def dimension(self, dimension_id: str) -> DimensionDefinition:
        try:
            return self.dimensions[dimension_id]
        except KeyError as exc:
            raise ValueError(f"unknown dimension: {dimension_id}") from exc

    def resolve_metric(self, text: str) -> list[MetricDefinition]:
        normalized = " ".join(text.lower().split())
        matches: list[MetricDefinition] = []
        for metric in self.bundle.metrics:
            candidates = [metric.metric_id, metric.display_name, *metric.aliases]
            if any(candidate.lower() in normalized for candidate in candidates):
                matches.append(metric)
        return matches

    def validate_filter(self, dimension_id: str, values: list[str]) -> None:
        dimension = self.dimension(dimension_id)
        if not values:
            raise ValueError(f"filter for {dimension_id} cannot be empty")
        unknown = sorted(set(values) - set(dimension.values))
        if unknown:
            raise ValueError(
                f"unknown value for {dimension_id}: {', '.join(unknown)}"
            )

