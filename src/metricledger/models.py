from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WireEnum(str, Enum):
    pass


class AdmissionStatus(WireEnum):
    ADMITTED = "ADMITTED"
    CLARIFY = "CLARIFY"
    REFUSED = "REFUSED"
    UNAVAILABLE = "UNAVAILABLE"


class Completeness(WireEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    NONE = "NONE"


class ClaimSupport(WireEnum):
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    INCONCLUSIVE = "INCONCLUSIVE"
    UNSUPPORTED = "UNSUPPORTED"


class MetricKind(WireEnum):
    ADDITIVE = "ADDITIVE"
    RATIO = "RATIO"


class ClaimType(WireEnum):
    VALUE = "VALUE"
    CHANGE = "CHANGE"
    CONTRIBUTION = "CONTRIBUTION"
    DIAGNOSTIC = "DIAGNOSTIC"
    QUALITY = "QUALITY"
    LIMITATION = "LIMITATION"


class QuestionKind(WireEnum):
    VALUE = "VALUE"
    CHANGE = "CHANGE"
    INVESTIGATE = "INVESTIGATE"


class IssueSeverity(WireEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    BLOCKER = "BLOCKER"


class QualityStatus(WireEnum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DateRange(Model):
    start: date
    end: date

    @model_validator(mode="after")
    def validate_order(self) -> DateRange:
        if self.end < self.start:
            raise ValueError("period end must not be before start")
        return self

    def day_count(self) -> int:
        return (self.end - self.start).days + 1


class BudgetPolicy(Model):
    max_queries: int = Field(default=4, ge=1, le=10)
    timeout_ms: int = Field(default=10_000, ge=100, le=60_000)
    max_planner_tokens: int = Field(default=2_000, ge=0, le=20_000)


class BudgetUsage(Model):
    queries_used: int = 0
    planner_tokens_used: int = 0
    elapsed_ms: int = 0


class DimensionDefinition(Model):
    dimension_id: str
    display_name: str
    column: str
    values: list[str]
    sensitive: bool = False


class FreshnessPolicy(Model):
    policy_id: str
    max_lag_hours: int = Field(ge=0)
    zero_fill_missing_groups: bool = False


class QualityPolicy(Model):
    policy_id: str
    max_null_rate: float = Field(default=0.0, ge=0, le=1)
    max_duplicate_rate: float = Field(default=0.0, ge=0, le=1)
    require_positive_denominator: bool = False


class MetricDefinition(Model):
    metric_id: str
    display_name: str
    kind: MetricKind
    expression: str
    numerator: str | None = None
    denominator: str | None = None
    time_column: str
    allowed_dimensions: list[str]
    aliases: list[str]
    freshness_policy_id: str
    quality_policy_id: str
    sensitivity: str = "internal"

    @model_validator(mode="after")
    def validate_ratio_fields(self) -> MetricDefinition:
        if self.kind is MetricKind.RATIO and (
            self.numerator is None or self.denominator is None
        ):
            raise ValueError("ratio metrics require numerator and denominator")
        return self


class SemanticBundle(Model):
    bundle_id: str
    version: str
    timezone: str
    allowed_tables: list[str]
    allowed_columns: list[str]
    dimensions: list[DimensionDefinition]
    metrics: list[MetricDefinition]
    freshness_policies: list[FreshnessPolicy]
    quality_policies: list[QualityPolicy]
    canonical_hash: str = ""


class SnapshotRecord(Model):
    snapshot_id: str
    table_name: str
    reporting_as_of: datetime
    period_start: date
    period_end: date
    complete: bool = True
    duplicate_rate: float = Field(default=0.0, ge=0, le=1)
    null_rate: float = Field(default=0.0, ge=0, le=1)
    manifest_hash: str

    @model_validator(mode="after")
    def normalize_datetime(self) -> SnapshotRecord:
        if self.reporting_as_of.tzinfo is None:
            raise ValueError("reporting_as_of must include a timezone")
        return self


class Scope(Model):
    values: dict[str, list[str]] = Field(default_factory=dict)


class Principal(Model):
    principal_id: str
    allowed_values: dict[str, list[str]]
    auth_policy_version: str = "local-demo-v1"


class AnalysisIntent(Model):
    metric_id: str
    question_kind: QuestionKind
    period: DateRange
    comparison_period: DateRange | None = None
    filters: dict[str, list[str]] = Field(default_factory=dict)
    candidate_dimensions: list[str] = Field(default_factory=list)


class AnalysisContext(Model):
    analysis_id: str
    intent: AnalysisIntent
    metric_id: str
    semantic_bundle_id: str
    semantic_bundle_hash: str
    snapshot_id: str
    snapshot_table: str
    snapshot_manifest_hash: str
    reporting_as_of: datetime
    timezone: str
    auth_policy_version: str
    effective_scope: Scope
    auth_scope_hash: str
    budget: BudgetPolicy
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AdmissionDecision(Model):
    status: AdmissionStatus
    issue_codes: list[str] = Field(default_factory=list)
    effective_scope: Scope | None = None
    snapshot: SnapshotRecord | None = None
    quality_checks: list[QualityCheck] = Field(default_factory=list)
    context: AnalysisContext | None = None


class AnalysisIssue(Model):
    code: str
    message: str
    severity: IssueSeverity = IssueSeverity.ERROR
    recoverable: bool = False


class QualityCheck(Model):
    code: str
    status: QualityStatus
    message: str
    value: float | int | str | None = None


class ClaimScope(Model):
    metric_id: str
    period: DateRange
    comparison_period: DateRange | None = None
    filters: dict[str, list[str]] = Field(default_factory=dict)
    group_by: str | None = None
    group_value: str | None = None


class Claim(Model):
    claim_id: str
    analysis_id: str
    claim_type: ClaimType
    template: str
    arguments: dict[str, str | int | float | None] = Field(default_factory=dict)
    evidence_refs: list[str]
    scope: ClaimScope
    limitations: list[str] = Field(default_factory=list)
    support: ClaimSupport = ClaimSupport.UNSUPPORTED


class ValidatedClaim(Model):
    claim: Claim
    rendered_text: str
    evidence_values: dict[str, str | int | float] = Field(default_factory=dict)


class ResultArtifact(Model):
    result_id: str
    analysis_id: str
    analysis_kind: str
    intent_dsl: AnalysisIntent
    compiled_sql: str
    compiled_parameters: list[str | int | float] = Field(default_factory=list)
    sql_fingerprint: str
    row_count: int = Field(ge=0)
    result_digest: str
    bounded_rows: list[dict[str, Any]]
    metric_versions: dict[str, str]
    snapshot_id: str
    semantic_bundle_hash: str
    scope: Scope
    quality_checks: list[QualityCheck] = Field(default_factory=list)
    elapsed_ms: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None


class SqlAudit(Model):
    result_id: str
    sql: str
    parameters: list[str | int | float]
    fingerprint: str
    row_count: int
    snapshot_id: str
    elapsed_ms: int


class CompiledQuery(Model):
    sql: str
    parameters: list[str | int | float]
    fingerprint: str
    referenced_tables: list[str]
    group_by: str | None = None


class QueryRows(Model):
    rows: list[dict[str, Any]]
    row_count: int
    elapsed_ms: int


class IntentParse(Model):
    status: AdmissionStatus
    intent: AnalysisIntent | None = None
    issue_codes: list[str] = Field(default_factory=list)
    message: str | None = None


class Hypothesis(Model):
    dimension_id: str
    rationale_code: str
    priority: int = Field(ge=0, le=2)


class ObservationSummary(Model):
    analysis_id: str
    metric_id: str
    metric_kind: MetricKind
    question_kind: QuestionKind
    candidate_dimensions: list[str] = Field(default_factory=list)
    evidence_summaries: list[str] = Field(default_factory=list)


class InvestigationOutcome(Model):
    analysis_id: str
    completeness: Completeness
    stop_reason: str
    queries_used: int = Field(ge=0)
    top_dimension: str | None = None
    claims: list[Claim] = Field(default_factory=list)
    artifacts: list[ResultArtifact] = Field(default_factory=list)
    issue_codes: list[str] = Field(default_factory=list)


class AnalysisRequest(Model):
    question: str = Field(min_length=1, max_length=1_000)
    as_of: datetime
    principal: Principal
    budget: BudgetPolicy = Field(default_factory=BudgetPolicy)

    @model_validator(mode="after")
    def validate_as_of(self) -> AnalysisRequest:
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must include a timezone")
        return self


class AnalysisResponse(Model):
    analysis_id: str
    admission: AdmissionDecision
    completeness: Completeness
    answer: str | None
    claims: list[ValidatedClaim] = Field(default_factory=list)
    issues: list[AnalysisIssue] = Field(default_factory=list)
    context: AnalysisContext | None = None
    budget_usage: BudgetUsage = Field(default_factory=BudgetUsage)
    evidence: list[ResultArtifact] = Field(default_factory=list)
    sql_audit: list[SqlAudit] = Field(default_factory=list)


def canonical_hash(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
