from __future__ import annotations

from uuid import uuid4

from .admission import AdmissionService
from .claims import ClaimRenderer, EvidenceValidator
from .compiler import QueryCompiler
from .data import DuckDbEngine
from .evidence import result_digest
from .intent import RuleBasedIntentParser
from .investigation import InvestigationService
from .models import (
    AdmissionDecision,
    AdmissionStatus,
    AnalysisIssue,
    AnalysisRequest,
    AnalysisResponse,
    Claim,
    ClaimScope,
    ClaimType,
    Completeness,
    IssueSeverity,
    MetricKind,
    ResultArtifact,
    SqlAudit,
    ValidatedClaim,
)
from .planners import HeuristicHypothesisPlanner, HypothesisPlanner
from .semantic import SemanticRegistry
from .snapshots import SnapshotRepository


class AnalysisService:
    def __init__(
        self,
        *,
        registry: SemanticRegistry,
        snapshots: SnapshotRepository,
        engine: DuckDbEngine,
        parser: RuleBasedIntentParser | None = None,
        planner: HypothesisPlanner | None = None,
        ledger=None,
    ):
        self.registry = registry
        self.snapshots = snapshots
        self.engine = engine
        self.parser = parser or RuleBasedIntentParser(registry)
        self.planner = planner or HeuristicHypothesisPlanner()
        self.ledger = ledger
        self.admission = AdmissionService(registry, snapshots)

    def analyze(self, request: AnalysisRequest) -> AnalysisResponse:
        parsed = self.parser.parse(request.question, as_of=request.as_of)
        if parsed.status is not AdmissionStatus.ADMITTED or parsed.intent is None:
            response = AnalysisResponse(
                analysis_id=f"an_{uuid4().hex[:16]}",
                admission=AdmissionDecision(
                    status=parsed.status,
                    issue_codes=parsed.issue_codes,
                ),
                completeness=Completeness.NONE,
                answer=None,
                issues=self._issues_for_codes(parsed.issue_codes),
            )
            return self._persist(response)

        decision = self.admission.admit(
            parsed.intent,
            request.principal,
            as_of=request.as_of,
            budget=request.budget,
        )
        if (
            decision.status is not AdmissionStatus.ADMITTED
            or decision.context is None
            or decision.snapshot is None
        ):
            response = AnalysisResponse(
                analysis_id=f"an_{uuid4().hex[:16]}",
                admission=decision,
                completeness=Completeness.NONE,
                answer=None,
                issues=self._issues_for_codes(decision.issue_codes),
            )
            return self._persist(response)

        context = decision.context
        metric = self.registry.metric(context.metric_id)
        compiler = QueryCompiler(self.registry)
        current, current_audit, current_issue = self._execute_scalar(
            context=context,
            period=context.intent.period,
            compiler=compiler,
            include_ratio_components=metric.kind is MetricKind.RATIO,
        )
        artifacts = [current]
        audits = [current_audit]
        issues = self._issues_for_codes(
            [current_issue] if current_issue is not None else []
        )
        queries_used = 1
        claims: list[Claim] = []

        current_row = current.bounded_rows[0] if current.row_count == 1 else {}
        current_value = current_row.get("metric_value")
        denominator = current_row.get("denominator_value")
        if (
            metric.kind is MetricKind.RATIO
            and isinstance(denominator, (int, float))
            and denominator <= 0
        ):
            issues.append(
                AnalysisIssue(
                    code="POSITIVE_DENOMINATOR",
                    message="比率分母存在零值",
                    severity=IssueSeverity.ERROR,
                    recoverable=False,
                )
            )
            response = AnalysisResponse(
                analysis_id=context.analysis_id,
                admission=decision,
                completeness=Completeness.NONE,
                answer=None,
                claims=[],
                issues=issues,
                context=context,
                evidence=artifacts,
                sql_audit=audits,
            )
            return self._persist(response)
        if not isinstance(current_value, (int, float)):
            issues.append(
                AnalysisIssue(
                    code="EMPTY_RESULT",
                    message="请求周期没有可支持结论的数据",
                    severity=IssueSeverity.WARNING,
                    recoverable=True,
                )
            )
            response = AnalysisResponse(
                analysis_id=context.analysis_id,
                admission=decision,
                completeness=Completeness.NONE,
                answer=None,
                claims=[],
                issues=issues,
                context=context,
                evidence=artifacts,
                sql_audit=audits,
            )
            return self._persist(response)

        baseline: ResultArtifact | None = None
        if (
            context.intent.comparison_period is not None
            and queries_used < context.budget.max_queries
        ):
            baseline, baseline_audit, baseline_issue = self._execute_scalar(
                context=context,
                period=context.intent.comparison_period,
                compiler=compiler,
                include_ratio_components=metric.kind is MetricKind.RATIO,
            )
            artifacts.append(baseline)
            audits.append(baseline_audit)
            queries_used += 1
            if baseline_issue is not None:
                issues.append(
                    self._issues_for_codes([baseline_issue])[0]
                )
            baseline_row = (
                baseline.bounded_rows[0] if baseline.row_count == 1 else {}
            )
            baseline_denominator = baseline_row.get("denominator_value")
            if (
                metric.kind is MetricKind.RATIO
                and isinstance(baseline_denominator, (int, float))
                and baseline_denominator <= 0
            ):
                issues.append(
                    AnalysisIssue(
                        code="POSITIVE_DENOMINATOR",
                        message="比率分母存在零值",
                        severity=IssueSeverity.ERROR,
                        recoverable=False,
                    )
                )
                response = AnalysisResponse(
                    analysis_id=context.analysis_id,
                    admission=decision,
                    completeness=Completeness.NONE,
                    answer=None,
                    claims=[],
                    issues=issues,
                    context=context,
                    evidence=artifacts,
                    sql_audit=audits,
                )
                return self._persist(response)

        headline_claim = self._headline_claim(
            context=context,
            current=current,
            baseline=baseline,
            metric_kind=metric.kind,
        )
        if headline_claim is not None:
            claims.append(headline_claim)

        completeness = Completeness.COMPLETE
        if context.intent.question_kind.value == "INVESTIGATE":
            investigation = InvestigationService(
                registry=self.registry,
                compiler=compiler,
                engine=self.engine,
                planner=self.planner,
                context=context,
                initial_artifacts=artifacts,
                initial_queries_used=queries_used,
            ).investigate()
            artifacts = investigation.artifacts
            claims.extend(investigation.claims)
            queries_used = investigation.queries_used
            completeness = investigation.completeness
            issues.extend(self._issues_for_codes(investigation.issue_codes))

        artifact_map = {
            artifact.result_id: artifact for artifact in artifacts
        }
        validator = EvidenceValidator(self.registry)
        renderer = ClaimRenderer()
        validated_claims: list[ValidatedClaim] = []
        for claim in claims:
            validated = validator.validate(claim, artifact_map)
            validated_claims.append(
                validated.model_copy(
                    update={"rendered_text": renderer.render(validated)}
                )
            )

        audits = [
            SqlAudit(
                result_id=artifact.result_id,
                sql=artifact.compiled_sql,
                parameters=artifact.compiled_parameters,
                fingerprint=artifact.sql_fingerprint,
                row_count=artifact.row_count,
                snapshot_id=artifact.snapshot_id,
                elapsed_ms=artifact.elapsed_ms,
            )
            for artifact in artifacts
        ]
        response = AnalysisResponse(
            analysis_id=context.analysis_id,
            admission=decision,
            completeness=completeness,
            answer="\n".join(
                claim.rendered_text for claim in validated_claims
            )
            or None,
            claims=validated_claims,
            issues=issues,
            context=context,
            budget_usage={
                "queries_used": queries_used,
                "planner_tokens_used": 0,
                "elapsed_ms": sum(audit.elapsed_ms for audit in audits),
            },
            evidence=artifacts,
            sql_audit=audits,
        )
        return self._persist(response)

    def get(
        self,
        analysis_id: str,
        *,
        tenant_id: str | None = None,
    ) -> AnalysisResponse | None:
        if self.ledger is None:
            return None
        return self.ledger.get(analysis_id, tenant_id=tenant_id)

    def _execute_scalar(
        self,
        *,
        context,
        period,
        compiler: QueryCompiler,
        include_ratio_components: bool,
    ) -> tuple[ResultArtifact, SqlAudit, str | None]:
        metric = self.registry.metric(context.metric_id)
        intent = context.intent.model_copy(update={"period": period})
        snapshot = self._snapshot_for_compiler(context)
        issue_code = None
        try:
            compiled = compiler.compile(
                intent,
                metric,
                snapshot,
                context.effective_scope,
                include_ratio_components=include_ratio_components,
            )
            rows = self.engine.execute(compiled, context.snapshot_id)
        except (ValueError, RuntimeError):
            compiled = compiler.compile(
                intent,
                metric,
                snapshot,
                context.effective_scope,
                include_ratio_components=include_ratio_components,
            )
            rows = type(
                "FailedRows",
                (),
                {"rows": [], "row_count": 0, "elapsed_ms": 0},
            )()
            issue_code = "HEADLINE_QUERY_FAILED"

        artifact = ResultArtifact(
            result_id=f"result_{uuid4().hex[:16]}",
            analysis_id=context.analysis_id,
            analysis_kind="headline",
            intent_dsl=intent,
            compiled_sql=compiled.sql,
            compiled_parameters=compiled.parameters,
            sql_fingerprint=compiled.fingerprint,
            row_count=rows.row_count,
            result_digest="",
            bounded_rows=rows.rows,
            metric_versions={metric.metric_id: self.registry.bundle.version},
            snapshot_id=context.snapshot_id,
            semantic_bundle_hash=context.semantic_bundle_hash,
            scope=context.effective_scope,
            elapsed_ms=rows.elapsed_ms,
        )
        artifact = artifact.model_copy(
            update={"result_digest": result_digest(artifact)}
        )
        audit = SqlAudit(
            result_id=artifact.result_id,
            sql=artifact.compiled_sql,
            parameters=artifact.compiled_parameters,
            fingerprint=artifact.sql_fingerprint,
            row_count=artifact.row_count,
            snapshot_id=artifact.snapshot_id,
            elapsed_ms=artifact.elapsed_ms,
        )
        return artifact, audit, issue_code

    def _headline_claim(
        self,
        *,
        context,
        current: ResultArtifact,
        baseline: ResultArtifact | None,
        metric_kind: MetricKind,
    ) -> Claim | None:
        metric = self.registry.metric(context.metric_id)
        if baseline is None:
            return Claim(
                claim_id=f"claim_{uuid4().hex[:16]}",
                analysis_id=context.analysis_id,
                claim_type=ClaimType.VALUE,
                template=(
                    f"{metric.display_name}当前值为 "
                    "{{evidence:metric_value}}。"
                ),
                evidence_refs=[current.result_id],
                scope=ClaimScope(
                    metric_id=context.metric_id,
                    period=context.intent.period,
                    filters=context.effective_scope.values,
                ),
            )
        return Claim(
            claim_id=f"claim_{uuid4().hex[:16]}",
            analysis_id=context.analysis_id,
            claim_type=ClaimType.CHANGE,
            template=(
                metric.display_name
                + "从 {{evidence:baseline_value}} "
                + "变为 {{evidence:metric_value}}，"
                + "变化 {{evidence:change_pct}}。"
            ),
            evidence_refs=[baseline.result_id, current.result_id],
            scope=ClaimScope(
                metric_id=context.metric_id,
                period=context.intent.period,
                comparison_period=context.intent.comparison_period,
                filters=context.effective_scope.values,
            ),
            limitations=(
                ["比率为方向性诊断，不使用可加贡献解释"]
                if metric_kind is MetricKind.RATIO
                else []
            ),
        )

    def _snapshot_for_compiler(self, context):
        from .models import SnapshotRecord

        comparison = context.intent.comparison_period
        return SnapshotRecord(
            snapshot_id=context.snapshot_id,
            table_name=context.snapshot_table,
            reporting_as_of=context.reporting_as_of,
            period_start=min(
                context.intent.period.start,
                comparison.start if comparison else context.intent.period.start,
            ),
            period_end=max(
                context.intent.period.end,
                comparison.end if comparison else context.intent.period.end,
            ),
            manifest_hash=context.snapshot_manifest_hash,
        )

    def _persist(self, response: AnalysisResponse) -> AnalysisResponse:
        if self.ledger is not None:
            self.ledger.save(response)
        return response

    @staticmethod
    def _issues_for_codes(codes: list[str]) -> list[AnalysisIssue]:
        messages = {
            "SCOPE_NOT_AUTHORIZED": "请求范围超出权限",
            "AMBIGUOUS_METRIC": "问题中的指标存在歧义",
            "MISSING_METRIC": "问题没有包含可识别指标",
            "MISSING_PERIOD": "问题没有包含明确时间范围",
            "AMBIGUOUS_PERIOD": "问题包含多个时间范围",
            "INVALID_PERIOD": "问题中的时间范围无效",
            "COMPARISON_NOT_COVERED": "固定快照未覆盖对比周期",
            "PERIOD_NOT_COVERED": "固定快照未覆盖请求周期",
            "SNAPSHOT_STALE": "固定快照已经过期",
            "SNAPSHOT_COMPLETE": "快照批次不完整",
            "DUPLICATE_RATE": "快照重复率超过质量阈值",
            "NULL_RATE": "快照空值率超过质量阈值",
            "BUDGET_EXHAUSTED": "调查查询预算已用尽",
            "DIAGNOSTIC_QUERY_FAILED": "诊断查询执行失败",
            "HEADLINE_QUERY_FAILED": "主指标查询执行失败",
            "COMPARISON_PERIOD_REQUIRED": "调查需要可比周期",
            "POSITIVE_DENOMINATOR": "比率分母存在零值",
        }
        return [
            AnalysisIssue(
                code=code,
                message=messages.get(code, code),
                severity=(
                    IssueSeverity.WARNING
                    if code == "BUDGET_EXHAUSTED"
                    else IssueSeverity.ERROR
                ),
                recoverable=code == "BUDGET_EXHAUSTED",
            )
            for code in codes
        ]
