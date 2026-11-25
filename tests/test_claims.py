import pytest

from metricledger.claims import ClaimRenderer, EvidenceValidator
from metricledger.evidence import EvidenceViolation
from metricledger.models import (
    ClaimSupport,
    ClaimType,
    DateRange,
)
from tests.conftest import scalar_artifact


def test_claim_cannot_cite_artifact_from_another_analysis(valid_claim, artifact):
    foreign = artifact.model_copy(update={"analysis_id": "another"})

    with pytest.raises(EvidenceViolation, match="cross-analysis"):
        EvidenceValidator().validate(valid_claim, {foreign.result_id: foreign})


def test_claim_scope_must_be_covered_by_evidence(valid_claim, artifacts):
    scoped = valid_claim.model_copy(
        update={
            "scope": valid_claim.scope.model_copy(
                update={"filters": {"region": ["west"]}}
            )
        }
    )

    with pytest.raises(EvidenceViolation, match="scope"):
        EvidenceValidator().validate(scoped, artifacts)


def test_renderer_uses_evidence_value_not_template_number(
    valid_claim, artifacts
):
    claim = valid_claim.model_copy(
        update={
            "template": (
                "支付 GMV 变化 {{evidence:change_pct}}，"
                "不是 999%。"
            )
        }
    )

    text = ClaimRenderer().render(
        EvidenceValidator().validate(claim, artifacts)
    )

    assert "999%" not in text
    assert "8.45%" in text


def test_validated_claim_recomputes_support_instead_of_trusting_input(
    valid_claim, artifacts
):
    untrusted = valid_claim.model_copy(update={"support": ClaimSupport.UNSUPPORTED})

    validated = EvidenceValidator().validate(untrusted, artifacts)

    assert validated.claim.support is ClaimSupport.SUPPORTED


def test_ratio_metric_cannot_claim_arithmetic_contribution(valid_claim, artifacts):
    ratio = valid_claim.model_copy(
        update={
            "claim_type": ClaimType.CONTRIBUTION,
            "template": "转化率下降主要来自 {{evidence:change_pct}}。",
            "scope": valid_claim.scope.model_copy(
                update={"metric_id": "paid_conversion_rate"}
            ),
        }
    )

    with pytest.raises(EvidenceViolation, match="ratio"):
        EvidenceValidator().validate(ratio, artifacts)


def test_claim_with_numeric_argument_is_rejected(valid_claim, artifacts):
    fabricated = valid_claim.model_copy(
        update={"arguments": {"change_pct": 88.8}}
    )

    with pytest.raises(EvidenceViolation, match="numeric"):
        EvidenceValidator().validate(fabricated, artifacts)


def test_claim_cannot_cite_missing_artifact(valid_claim, artifacts):
    artifacts.pop("result-current")

    with pytest.raises(EvidenceViolation, match="missing evidence"):
        EvidenceValidator().validate(valid_claim, artifacts)


def test_claim_cannot_mix_snapshots(valid_claim, artifact_pair):
    baseline, current = artifact_pair
    foreign = scalar_artifact(
        result_id="result-foreign-snapshot",
        period=DateRange(start=current.intent_dsl.period.start, end=current.intent_dsl.period.end),
        metric_value=1.0,
        snapshot_id="batch_20261116_2300",
    )
    claim = valid_claim.model_copy(
        update={"evidence_refs": [baseline.result_id, foreign.result_id]}
    )

    with pytest.raises(EvidenceViolation, match="snapshot"):
        EvidenceValidator().validate(
            claim,
            {baseline.result_id: baseline, foreign.result_id: foreign},
        )


def test_unknown_placeholder_cannot_reach_renderer(valid_claim, artifacts):
    claim = valid_claim.model_copy(
        update={"template": "GMV 变化 {{evidence:invented_value}}。"}
    )

    with pytest.raises(EvidenceViolation, match="placeholder"):
        EvidenceValidator().validate(claim, artifacts)
