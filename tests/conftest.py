from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from metricledger.models import (
    AnalysisIntent,
    Claim,
    ClaimScope,
    ClaimType,
    DateRange,
    QuestionKind,
    ResultArtifact,
    Scope,
    canonical_hash,
)


TZ = ZoneInfo("Asia/Shanghai")
CURRENT_PERIOD = DateRange(start=date(2026, 11, 17), end=date(2026, 11, 23))
PREVIOUS_PERIOD = DateRange(start=date(2026, 11, 10), end=date(2026, 11, 16))
FULL_FILTERS = {
    "region": ["east"],
    "channel": ["app", "web", "mini_program"],
    "category": ["electronics", "beauty", "home", "fashion"],
}


def seal_artifact(artifact: ResultArtifact) -> ResultArtifact:
    payload = artifact.model_dump(
        mode="json",
        exclude={"result_id", "result_digest"},
    )
    return artifact.model_copy(update={"result_digest": canonical_hash(payload)})


def scalar_artifact(
    *,
    result_id: str,
    period: DateRange,
    metric_value: float,
    analysis_id: str = "analysis-1",
    snapshot_id: str = "batch_20261123_2300",
    semantic_hash: str = "semantic-hash-1",
    metric_id: str = "paid_gmv",
    filters: dict[str, list[str]] | None = None,
    scope_values: dict[str, list[str]] | None = None,
    comparison_period: DateRange | None = None,
) -> ResultArtifact:
    artifact = ResultArtifact(
        result_id=result_id,
        analysis_id=analysis_id,
        analysis_kind="headline",
        intent_dsl=AnalysisIntent(
            metric_id=metric_id,
            question_kind=QuestionKind.VALUE,
            period=period,
            comparison_period=comparison_period,
            filters=filters or {},
        ),
        compiled_sql="SELECT metric_value FROM fixed_snapshot",
        sql_fingerprint=f"fingerprint-{result_id}",
        row_count=1,
        result_digest="",
        bounded_rows=[{"metric_value": metric_value}],
        metric_versions={metric_id: "1.0.0"},
        snapshot_id=snapshot_id,
        semantic_bundle_hash=semantic_hash,
        scope=Scope(values=scope_values or FULL_FILTERS),
        created_at=datetime(2026, 11, 23, 23, 59, 59, tzinfo=TZ),
    )
    return seal_artifact(artifact)


@pytest.fixture
def artifact_pair() -> tuple[ResultArtifact, ResultArtifact]:
    baseline = scalar_artifact(
        result_id="result-baseline",
        period=PREVIOUS_PERIOD,
        metric_value=800_000.0,
    )
    current = scalar_artifact(
        result_id="result-current",
        period=CURRENT_PERIOD,
        metric_value=732_400.0,
        comparison_period=PREVIOUS_PERIOD,
    )
    return baseline, current


@pytest.fixture
def artifact(artifact_pair: tuple[ResultArtifact, ResultArtifact]) -> ResultArtifact:
    return artifact_pair[1]


@pytest.fixture
def artifacts(
    artifact_pair: tuple[ResultArtifact, ResultArtifact],
) -> dict[str, ResultArtifact]:
    baseline, current = artifact_pair
    return {baseline.result_id: baseline, current.result_id: current}


@pytest.fixture
def valid_claim(artifact_pair: tuple[ResultArtifact, ResultArtifact]) -> Claim:
    baseline, current = artifact_pair
    return Claim(
        claim_id="claim-change",
        analysis_id="analysis-1",
        claim_type=ClaimType.CHANGE,
        template=(
            "支付 GMV 从 {{evidence:baseline_value}} 变为 "
            "{{evidence:metric_value}}，变化 {{evidence:change_pct}}。"
        ),
        evidence_refs=[baseline.result_id, current.result_id],
        scope=ClaimScope(
            metric_id="paid_gmv",
            period=CURRENT_PERIOD,
            comparison_period=PREVIOUS_PERIOD,
            filters=FULL_FILTERS,
        ),
    )
