from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .evidence import EvidenceViolation, assert_artifact_integrity
from .models import (
    Claim,
    ClaimSupport,
    ClaimType,
    MetricKind,
    ResultArtifact,
    ValidatedClaim,
)
from .semantic import SemanticRegistry


PLACEHOLDER_PATTERN = re.compile(r"\{\{([^{}]+)\}\}")
NUMERIC_LITERAL_PATTERN = re.compile(
    r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?(?![\w.])"
)
ALLOWED_EVIDENCE_KEYS = {
    "metric_value",
    "baseline_value",
    "change_abs",
    "change_pct",
    "dimension_value",
    "numerator_value",
    "denominator_value",
    "contribution_abs",
    "contribution_share_pct",
}


class EvidenceValidator:
    def __init__(self, registry: SemanticRegistry | None = None):
        self.registry = registry or SemanticRegistry.load_default()

    def validate(
        self,
        claim: Claim,
        artifacts: dict[str, ResultArtifact],
        *,
        now: datetime | None = None,
    ) -> ValidatedClaim:
        self._reject_numeric_arguments(claim)
        placeholders = self._parse_placeholders(claim)
        metric = self.registry.metric(claim.scope.metric_id)
        if metric.kind is MetricKind.RATIO and claim.claim_type is ClaimType.CONTRIBUTION:
            raise EvidenceViolation(
                "ratio metrics cannot support arithmetic contribution claims"
            )

        cited = self._resolve_citations(claim, artifacts, now=now)
        self._validate_context(claim, cited)
        self._validate_scope(claim, cited)
        evidence_values = self._build_evidence_values(claim, cited)
        missing = sorted(set(placeholders) - set(evidence_values))
        if missing:
            raise EvidenceViolation(
                f"evidence placeholder has no value: {missing[0]}"
            )

        values = {
            key: evidence_values[key]
            for key in placeholders
        }
        return ValidatedClaim(
            claim=claim.model_copy(update={"support": ClaimSupport.SUPPORTED}),
            rendered_text="",
            evidence_values=values,
        )

    @staticmethod
    def _reject_numeric_arguments(claim: Claim) -> None:
        for key, value in claim.arguments.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                raise EvidenceViolation(
                    f"numeric argument cannot be a claim data source: {key}"
                )

    @staticmethod
    def _parse_placeholders(claim: Claim) -> list[str]:
        if not claim.evidence_refs:
            raise EvidenceViolation("claim has no evidence references")
        placeholders: list[str] = []
        for token in PLACEHOLDER_PATTERN.findall(claim.template):
            prefix, separator, key = token.partition(":")
            if prefix != "evidence" or not separator or not key:
                raise EvidenceViolation(
                    f"unsupported claim placeholder: {token}"
                )
            if key not in ALLOWED_EVIDENCE_KEYS:
                raise EvidenceViolation(
                    f"unknown evidence placeholder: {key}"
                )
            placeholders.append(key)
        return placeholders

    def _resolve_citations(
        self,
        claim: Claim,
        artifacts: dict[str, ResultArtifact],
        *,
        now: datetime | None,
    ) -> list[ResultArtifact]:
        cited_present = [
            artifacts[result_id]
            for result_id in claim.evidence_refs
            if result_id in artifacts
        ]
        if any(
            artifact.analysis_id != claim.analysis_id
            for artifact in cited_present
        ):
            raise EvidenceViolation(
                "cross-analysis evidence reference is not allowed"
            )

        missing = [
            result_id
            for result_id in claim.evidence_refs
            if result_id not in artifacts
        ]
        if missing:
            raise EvidenceViolation(f"missing evidence: {missing[0]}")

        cited = [artifacts[result_id] for result_id in claim.evidence_refs]
        checked_at = now or datetime.now(timezone.utc)
        for artifact in cited:
            assert_artifact_integrity(artifact)
            if artifact.analysis_id != claim.analysis_id:
                raise EvidenceViolation(
                    "cross-analysis evidence reference is not allowed"
                )
            if artifact.expires_at is not None and artifact.expires_at <= checked_at:
                raise EvidenceViolation(
                    f"evidence expired: {artifact.result_id}"
                )
            if claim.claim_type not in {ClaimType.QUALITY, ClaimType.LIMITATION}:
                if artifact.row_count == 0:
                    raise EvidenceViolation(
                        "empty query result cannot support a data claim"
                    )
        return cited

    @staticmethod
    def _validate_context(
        claim: Claim,
        artifacts: list[ResultArtifact],
    ) -> None:
        snapshots = {artifact.snapshot_id for artifact in artifacts}
        if len(snapshots) != 1:
            raise EvidenceViolation(
                "all evidence must use the same fixed snapshot"
            )
        semantic_hashes = {
            artifact.semantic_bundle_hash for artifact in artifacts
        }
        if len(semantic_hashes) != 1:
            raise EvidenceViolation(
                "all evidence must use the same semantic bundle"
            )
        for artifact in artifacts:
            if artifact.intent_dsl.metric_id != claim.scope.metric_id:
                raise EvidenceViolation(
                    "evidence metric does not match claim metric"
                )
            if claim.scope.group_by is not None:
                if claim.scope.group_by not in artifact.intent_dsl.candidate_dimensions:
                    raise EvidenceViolation(
                        "evidence was not grouped by the claimed dimension"
                    )

    @staticmethod
    def _validate_scope(
        claim: Claim,
        artifacts: list[ResultArtifact],
    ) -> None:
        for artifact in artifacts:
            for dimension_id, requested in claim.scope.filters.items():
                available = artifact.scope.values.get(dimension_id, [])
                if not set(requested).issubset(set(available)):
                    raise EvidenceViolation(
                        f"evidence scope does not cover claim scope: {dimension_id}"
                    )
            if claim.scope.group_by is not None:
                values = {
                    row.get("dimension_value")
                    for row in artifact.bounded_rows
                    if "dimension_value" in row
                }
                if claim.scope.group_value not in values:
                    raise EvidenceViolation(
                        "evidence rows do not cover the claimed group"
                    )

    @staticmethod
    def _build_evidence_values(
        claim: Claim,
        artifacts: list[ResultArtifact],
    ) -> dict[str, str | int | float]:
        values: dict[str, str | int | float] = {}
        current_artifact = next(
            (
                artifact
                for artifact in artifacts
                if artifact.intent_dsl.period == claim.scope.period
                and EvidenceValidator._row_for_claim(artifact, claim) is not None
            ),
            None,
        )
        if current_artifact is not None:
            current_row = EvidenceValidator._row_for_claim(
                current_artifact, claim
            )
            if isinstance(current_row.get("metric_value"), (int, float)):
                values["metric_value"] = current_row["metric_value"]
            for key in ("numerator_value", "denominator_value"):
                if isinstance(current_row.get(key), (int, float)):
                    values[key] = current_row[key]

        if claim.scope.comparison_period is not None:
            baseline_artifact = next(
                (
                    artifact
                    for artifact in artifacts
                    if artifact.intent_dsl.period == claim.scope.comparison_period
                    and EvidenceValidator._row_for_claim(artifact, claim) is not None
                ),
                None,
            )
            if baseline_artifact is not None:
                baseline_row = EvidenceValidator._row_for_claim(
                    baseline_artifact, claim
                )
                baseline_value = baseline_row.get("metric_value")
                if isinstance(baseline_value, (int, float)):
                    values["baseline_value"] = baseline_value

        metric_value = values.get("metric_value")
        baseline_value = values.get("baseline_value")
        if isinstance(metric_value, (int, float)) and isinstance(
            baseline_value, (int, float)
        ):
            values["change_abs"] = metric_value - baseline_value
            if baseline_value != 0:
                values["change_pct"] = (
                    (metric_value - baseline_value) / abs(baseline_value) * 100
                )

        if claim.claim_type is ClaimType.CONTRIBUTION and claim.scope.group_by:
            current_grouped = next(
                (
                    artifact
                    for artifact in artifacts
                    if artifact.intent_dsl.period == claim.scope.period
                    and artifact.intent_dsl.candidate_dimensions
                    and artifact.row_count > 0
                ),
                None,
            )
            baseline_grouped = next(
                (
                    artifact
                    for artifact in artifacts
                    if artifact.intent_dsl.period == claim.scope.comparison_period
                    and artifact.intent_dsl.candidate_dimensions
                    and artifact.row_count > 0
                ),
                None,
            )
            if current_grouped is not None and baseline_grouped is not None:
                current_groups = EvidenceValidator._group_metric_values(
                    current_grouped
                )
                baseline_groups = EvidenceValidator._group_metric_values(
                    baseline_grouped
                )
                total_delta = sum(
                    current_groups.values()
                ) - sum(baseline_groups.values())
                group_delta = current_groups.get(
                    claim.scope.group_value, 0.0
                ) - baseline_groups.get(claim.scope.group_value, 0.0)
                values["contribution_abs"] = group_delta
                if total_delta != 0:
                    values["contribution_share_pct"] = (
                        group_delta / total_delta * 100
                    )

        for key in ALLOWED_EVIDENCE_KEYS - set(values):
            distinct = {
                row.get(key)
                for artifact in artifacts
                for row in artifact.bounded_rows
                if isinstance(row.get(key), (str, int, float))
                and not isinstance(row.get(key), bool)
            }
            if len(distinct) == 1:
                values[key] = distinct.pop()
        return values

    @staticmethod
    def _row_for_claim(
        artifact: ResultArtifact,
        claim: Claim,
    ) -> dict[str, Any] | None:
        if claim.scope.group_by is None:
            return artifact.bounded_rows[0] if artifact.row_count == 1 else None
        return next(
            (
                row
                for row in artifact.bounded_rows
                if row.get("dimension_value") == claim.scope.group_value
            ),
            None,
        )

    @staticmethod
    def _group_metric_values(
        artifact: ResultArtifact,
    ) -> dict[str, float]:
        values: dict[str, float] = {}
        for row in artifact.bounded_rows:
            group_value = row.get("dimension_value")
            metric_value = row.get("metric_value")
            if isinstance(group_value, str) and isinstance(
                metric_value, (int, float)
            ):
                values[group_value] = float(metric_value)
        return values


class ClaimRenderer:
    def render(self, validated_claim: ValidatedClaim) -> str:
        template = validated_claim.claim.template
        values = validated_claim.evidence_values
        cursor = 0
        rendered: list[str] = []
        for match in PLACEHOLDER_PATTERN.finditer(template):
            rendered.append(
                self._sanitize_static_text(template[cursor : match.start()])
            )
            prefix, separator, key = match.group(1).partition(":")
            if prefix != "evidence" or not separator or key not in values:
                raise EvidenceViolation(
                    f"unresolved claim placeholder: {match.group(1)}"
                )
            rendered.append(self._format_value(key, values[key]))
            cursor = match.end()
        rendered.append(self._sanitize_static_text(template[cursor:]))
        return "".join(rendered).strip()

    @staticmethod
    def _sanitize_static_text(text: str) -> str:
        return NUMERIC_LITERAL_PATTERN.sub("", text)

    @staticmethod
    def _format_value(key: str, value: str | int | float) -> str:
        if isinstance(value, str):
            return value
        if key.endswith("_pct"):
            return f"{value:.2f}%"
        if isinstance(value, int):
            return f"{value:,}"
        return f"{value:,.2f}"
