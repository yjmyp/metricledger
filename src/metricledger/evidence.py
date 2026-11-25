from __future__ import annotations

from .models import ResultArtifact, canonical_hash


class EvidenceViolation(ValueError):
    """Raised when evidence is missing, stale, tampered with, or out of context."""


def result_digest(artifact: ResultArtifact) -> str:
    """Compute the integrity digest for the material result payload."""

    payload = artifact.model_dump(
        mode="json",
        exclude={"result_id", "result_digest"},
    )
    return canonical_hash(payload)


def assert_artifact_integrity(artifact: ResultArtifact) -> None:
    if artifact.row_count != len(artifact.bounded_rows):
        raise EvidenceViolation("artifact row count does not match bounded rows")
    if artifact.row_count > 200:
        raise EvidenceViolation("artifact exceeds the 200-row evidence limit")
    if result_digest(artifact) != artifact.result_digest:
        raise EvidenceViolation("artifact digest does not match its payload")


class EvidenceStore:
    """Process-local evidence registry with collision and integrity checks."""

    def __init__(self) -> None:
        self._results: dict[str, ResultArtifact] = {}

    def add_result(self, artifact: ResultArtifact) -> ResultArtifact:
        existing = self._results.get(artifact.result_id)
        if existing is not None:
            if existing == artifact:
                return existing
            raise EvidenceViolation(
                f"result id collision: {artifact.result_id}"
            )
        assert_artifact_integrity(artifact)
        self._results[artifact.result_id] = artifact
        return artifact

    def get(self, result_id: str) -> ResultArtifact | None:
        return self._results.get(result_id)

    def snapshot(self) -> dict[str, ResultArtifact]:
        return dict(self._results)
