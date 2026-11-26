from __future__ import annotations

from .models import AnalysisContext, SemanticBundle, canonical_hash
from .semantic import SemanticRegistry
from .snapshots import SnapshotRepository


class ContextIntegrityError(ValueError):
    def __init__(self, kind: str):
        super().__init__(f"{kind} context drift detected")
        self.kind = kind


class ContextIntegrityGuard:
    def __init__(
        self,
        registry: SemanticRegistry,
        snapshots: SnapshotRepository,
    ):
        self.registry = registry
        self.snapshots = snapshots

    def verify(self, context: AnalysisContext) -> None:
        current_bundle = SemanticBundle.model_validate(
            self.registry.bundle.model_copy(
                update={
                    "metrics": list(self.registry.metrics.values()),
                    "dimensions": list(self.registry.dimensions.values()),
                    "freshness_policies": list(
                        self.registry.freshness_policies.values()
                    ),
                    "quality_policies": list(
                        self.registry.quality_policies.values()
                    ),
                }
            ).model_dump(
                mode="json",
                exclude={"canonical_hash"},
            )
        )
        current_hash = canonical_hash(current_bundle.model_dump(mode="json"))
        if current_hash != context.semantic_bundle_hash:
            raise ContextIntegrityError("semantic")
        try:
            snapshot = self.snapshots.get(context.snapshot_id)
        except ValueError as exc:
            raise ContextIntegrityError("snapshot") from exc
        if snapshot.manifest_hash != context.snapshot_manifest_hash:
            raise ContextIntegrityError("snapshot")
