import pytest

from metricledger.evidence import EvidenceStore, EvidenceViolation


def test_store_accepts_integrity_checked_artifact(artifact):
    store = EvidenceStore()

    stored = store.add_result(artifact)

    assert stored == artifact
    assert store.get(artifact.result_id) == artifact


def test_store_rejects_artifact_whose_rows_changed_after_sealing(artifact):
    tampered = artifact.model_copy(
        update={"bounded_rows": [{"metric_value": 999_999.0}]}
    )

    with pytest.raises(EvidenceViolation, match="digest"):
        EvidenceStore().add_result(tampered)


def test_store_rejects_result_id_collision(artifact):
    store = EvidenceStore()
    store.add_result(artifact)
    foreign = artifact.model_copy(
        update={
            "analysis_id": "analysis-2",
            "bounded_rows": [{"metric_value": 1.0}],
        }
    )

    with pytest.raises(EvidenceViolation, match="collision"):
        store.add_result(foreign)
