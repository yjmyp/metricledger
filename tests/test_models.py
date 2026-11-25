from metricledger.models import (
    AdmissionStatus,
    ClaimSupport,
    MetricKind,
    canonical_hash,
)


def test_enums_have_stable_wire_values():
    assert AdmissionStatus.ADMITTED.value == "ADMITTED"
    assert ClaimSupport.SUPPORTED.value == "SUPPORTED"
    assert MetricKind.RATIO.value == "RATIO"


def test_canonical_hash_is_independent_of_mapping_order():
    assert canonical_hash({"b": 2, "a": 1}) == canonical_hash({"a": 1, "b": 2})
