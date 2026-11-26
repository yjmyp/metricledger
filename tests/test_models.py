import pytest

from metricledger.models import (
    AdmissionStatus,
    BudgetUsage,
    ClaimSupport,
    EvidenceStatus,
    MetricKind,
    ModelUsage,
    canonical_hash,
)


def test_enums_have_stable_wire_values():
    assert AdmissionStatus.ADMITTED.value == "ADMITTED"
    assert ClaimSupport.SUPPORTED.value == "SUPPORTED"
    assert MetricKind.RATIO.value == "RATIO"


def test_canonical_hash_is_independent_of_mapping_order():
    assert canonical_hash({"b": 2, "a": 1}) == canonical_hash({"a": 1, "b": 2})


def test_unknown_planner_token_usage_is_not_encoded_as_zero():
    usage = BudgetUsage(planner_tokens_used=None, planner_usage_complete=False)

    assert usage.planner_tokens_used is None
    assert usage.planner_usage_complete is False


def test_model_usage_rejects_negative_tokens():
    with pytest.raises(ValueError):
        ModelUsage(prompt_tokens=1, completion_tokens=-1, total_tokens=0)


def test_evidence_status_wire_values_are_stable():
    assert EvidenceStatus.MATERIAL_CHANGE.value == "MATERIAL_CHANGE"
    assert EvidenceStatus.QUERY_FAILED.value == "QUERY_FAILED"
