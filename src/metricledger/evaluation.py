from __future__ import annotations

import json
import math
import platform
import re
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import duckdb

from .data import DuckDbEngine
from .models import (
    AdmissionStatus,
    AnalysisRequest,
    AnalysisResponse,
    BudgetPolicy,
    ClaimSupport,
    Completeness,
    Principal,
)
from .semantic import SemanticRegistry
from .service import AnalysisService
from .snapshots import SnapshotRepository


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIOS = PROJECT_ROOT / "eval" / "scenarios.json"
METRICLEDGER = "metricledger"
SEMANTIC_ONLY = "semantic_without_investigation"
NAIVE = "naive_text_to_sql"
BASELINES = (NAIVE, SEMANTIC_ONLY, METRICLEDGER)


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    category: str
    question: str
    as_of: datetime
    principal: Principal
    expected_status: AdmissionStatus
    expected_completeness: Completeness
    requires_investigation: bool


@dataclass(frozen=True)
class EvaluationSuite:
    scenarios: tuple[Scenario, ...]
    source_path: Path
    version: int


@dataclass(frozen=True)
class EvaluationOutcome:
    status: AdmissionStatus
    completeness: Completeness
    answer: str | None
    claim_count: int
    supported_claim_count: int
    evidence_bound_claim_count: int
    claim_types: tuple[str, ...]
    zero_filled: bool
    queries_used: int
    elapsed_ms: float


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    category: str
    expected_status: str
    status_matches: bool
    scenario_success: bool
    unsafe_answer: bool
    scope_violation: bool
    zero_as_empty: bool
    unsupported_claim_count: int
    evidence_violation_count: int
    budget_violation: bool
    outcome: EvaluationOutcome


@dataclass(frozen=True)
class BaselineMetrics:
    baseline: str
    scenario_count: int
    status_accuracy_count: int
    scenario_success_count: int
    unsafe_answer_count: int
    scope_violation_count: int
    zero_as_empty_count: int
    unsupported_claim_count: int
    evidence_violation_count: int
    budget_violation_count: int
    supported_investigation_count: int
    completed_supported_investigation_count: int
    total_claim_count: int
    evidence_bound_claim_count: int
    elapsed_p50_ms: float
    elapsed_p95_ms: float
    total_queries: int

    @property
    def status_accuracy(self) -> float:
        return _ratio(self.status_accuracy_count, self.scenario_count)

    @property
    def scenario_success_rate(self) -> float:
        return _ratio(self.scenario_success_count, self.scenario_count)

    @property
    def evidence_bound_claim_rate(self) -> float:
        return _ratio(self.evidence_bound_claim_count, self.total_claim_count)

    @property
    def investigation_completion_rate(self) -> float:
        return _ratio(
            self.completed_supported_investigation_count,
            self.supported_investigation_count,
        )

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload.update(
            {
                "status_accuracy": self.status_accuracy,
                "scenario_success_rate": self.scenario_success_rate,
                "evidence_bound_claim_rate": self.evidence_bound_claim_rate,
                "investigation_completion_rate": self.investigation_completion_rate,
            }
        )
        return payload


@dataclass(frozen=True)
class EvaluationReport:
    generated_at: str
    commit: str
    worktree_dirty: bool
    source_path: str
    scenario_count: int
    category_counts: dict[str, int]
    baselines: dict[str, BaselineMetrics]
    environment: dict[str, str | int]
    results: dict[str, tuple[ScenarioResult, ...]]

    def to_dict(self) -> dict:
        return {
            "status": "COMPLETE",
            "generated_at": self.generated_at,
            "commit": self.commit,
            "worktree_dirty": self.worktree_dirty,
            "source_path": self.source_path,
            "scope": "本仓库合成场景",
            "llm_judge_used": False,
            "scenario_count": self.scenario_count,
            "category_counts": self.category_counts,
            "environment": self.environment,
            "baselines": {
                name: metrics.to_dict()
                for name, metrics in self.baselines.items()
            },
            "results": {
                name: [asdict(result) for result in results]
                for name, results in self.results.items()
            },
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        )

    def to_markdown(self) -> str:
        lines = [
            "# MetricLedger Evaluation Report",
            "",
            "> 本报告只评估“本仓库合成场景”，不是外部基准，也不能外推为公开榜单结论。",
            "> 安全指标由确定性规则计算，未使用 LLM judge。",
            "",
            f"- 生成时间：`{self.generated_at}`",
            f"- 提交：`{self.commit}`",
            f"- 工作区是否有未提交改动：`{str(self.worktree_dirty).lower()}`",
            f"- 场景文件：`{self.source_path}`",
            f"- 精确场景数：**{self.scenario_count}**",
            f"- Python：`{self.environment['python_version']}`",
            f"- DuckDB：`{self.environment['duckdb_version']}`",
            f"- 固定快照行数：**{self.environment['snapshot_row_count']}**",
            "",
            "## 场景分布",
            "",
            "| 类别 | 数量 |",
            "|---|---:|",
        ]
        for category, count in sorted(self.category_counts.items()):
            lines.append(f"| `{category}` | {count} |")

        lines.extend(
            [
                "",
                "## 基线与结果",
                "",
                "| 基线 | 场景成功 | 状态准确率 | 调查完整率 | 不安全回答 | 越权回答 | 空结果冒充 0 | 无证据 Claim | 证据违规 | 预算违规 | P95 延迟 |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for name in BASELINES:
            metrics = self.baselines[name]
            lines.append(
                "| `{}` | {}/{} ({:.1%}) | {:.1%} | {}/{} ({:.1%}) | {} | {} | {} | {} | {} | {} | {:.2f} ms |".format(
                    name,
                    metrics.scenario_success_count,
                    metrics.scenario_count,
                    metrics.scenario_success_rate,
                    metrics.status_accuracy,
                    metrics.completed_supported_investigation_count,
                    metrics.supported_investigation_count,
                    metrics.investigation_completion_rate,
                    metrics.unsafe_answer_count,
                    metrics.scope_violation_count,
                    metrics.zero_as_empty_count,
                    metrics.unsupported_claim_count,
                    metrics.evidence_violation_count,
                    metrics.budget_violation_count,
                    metrics.elapsed_p95_ms,
                )
            )

        metrics = self.baselines[METRICLEDGER]
        partial_count = (
            metrics.supported_investigation_count
            - metrics.completed_supported_investigation_count
        )
        lines.extend(
            [
                "",
                "## 调查完整率口径",
                "",
                (
                    f"`metricledger` 的 `{metrics.completed_supported_investigation_count}/"
                    f"{metrics.supported_investigation_count}` 表示支持调查的场景中有 "
                    f"{metrics.completed_supported_investigation_count} 个实际得到 "
                    f"`COMPLETE`；另外 {partial_count} 个场景在评测标签中明确要求 "
                    "`PARTIAL`，且状态、阻断结论和安全边界都正确，因此仍计为成功场景。"
                ),
                "",
                "## 基线定义",
                "",
                "- `naive_text_to_sql`：确定性不安全对照组。绕过原请求权限；无法解析或没有覆盖时返回“结果为 0”，用于暴露常见失败模式。它不是具名商用产品或真实 LLM 的分数。",
                "- `semantic_without_investigation`：复用语义解析、准入、固定快照和证据校验，但把查询预算限制为 2，只产出主结论，不执行分组调查。",
                "- `metricledger`：完整准入、固定快照、Claim 证据校验和有界分组调查，默认最多 4 次查询。",
                "",
                "## 成功判定",
                "",
                "一个场景只有同时满足以下条件才算成功：",
                "",
                "1. 准入状态与场景标签一致。",
                "2. 完整度与场景标签一致。",
                "3. 支持调查的场景至少包含主结论和一个分组诊断 Claim。",
                "4. 拒绝、澄清和不可用场景不得返回答案或 Claim。",
                "",
                "## 已知限制",
                "",
                "- 数据是确定性合成电商数据，不代表真实企业流量或收益。",
                "- `naive_text_to_sql` 是故意不安全的对照实现，仅用于验证防线是否真的拦截，不是对某一家产品的评测。",
                "- 语义解析器只覆盖当前资源包中的三个指标、三个维度和明确 ISO 日期。",
                "- 延迟是本地单进程测量，受机器负载影响，不能作为线上 SLA。",
                "- “不伪造数字、不越权返回、不把失败冒充零值”由代码和测试保证；自然语言理解本身仍可能触发澄清，因此不承诺所有问题都能自动作答。",
            ]
        )
        return "\n".join(lines) + "\n"


def load_suite(path: str | Path | None = None) -> EvaluationSuite:
    source = Path(path) if path is not None else DEFAULT_SCENARIOS
    payload = json.loads(source.read_text(encoding="utf-8"))
    principals = {
        name: Principal.model_validate(value)
        for name, value in payload["principals"].items()
    }
    scenarios: list[Scenario] = []
    for group_index, group in enumerate(payload["groups"], start=1):
        principal = principals[group["principal"]]
        partial_questions = set(group.get("partial_questions", []))
        for question_index, question in enumerate(group["questions"], start=1):
            scenarios.append(
                Scenario(
                    scenario_id=(
                        f"{group['category']}-{group_index:02d}-{question_index:03d}"
                    ),
                    category=group["category"],
                    question=question,
                    as_of=datetime.fromisoformat(group["as_of"]),
                    principal=principal,
                    expected_status=AdmissionStatus(group["expected_status"]),
                    expected_completeness=Completeness(
                        "PARTIAL"
                        if question in partial_questions
                        else group["expected_completeness"]
                    ),
                    requires_investigation=bool(group["requires_investigation"]),
                )
            )
    keys = {
        (
            scenario.question,
            scenario.as_of.isoformat(),
            scenario.principal.principal_id,
        )
        for scenario in scenarios
    }
    if len(keys) != len(scenarios):
        raise ValueError("evaluation scenarios must be unique")
    return EvaluationSuite(
        scenarios=tuple(scenarios),
        source_path=source,
        version=int(payload["version"]),
    )


def run_evaluation(
    suite: EvaluationSuite,
    *,
    database_path: str | Path,
    registry: SemanticRegistry,
    snapshots: SnapshotRepository,
    commit: str,
    worktree_dirty: bool,
) -> EvaluationReport:
    metricledger_service = _service(database_path, registry, snapshots)
    semantic_service = _service(database_path, registry, snapshots)
    results: dict[str, list[ScenarioResult]] = {
        name: [] for name in BASELINES
    }

    for scenario in suite.scenarios:
        start = time.perf_counter()
        outcome = _metricledger_outcome(
            metricledger_service,
            scenario,
            budget=BudgetPolicy(max_queries=4),
        )
        elapsed = (time.perf_counter() - start) * 1000
        outcome = _with_elapsed(outcome, elapsed)
        results[METRICLEDGER].append(_scenario_result(scenario, outcome))

        start = time.perf_counter()
        outcome = _metricledger_outcome(
            semantic_service,
            scenario,
            budget=BudgetPolicy(max_queries=2),
        )
        elapsed = (time.perf_counter() - start) * 1000
        outcome = _with_elapsed(outcome, elapsed)
        results[SEMANTIC_ONLY].append(_scenario_result(scenario, outcome))

        start = time.perf_counter()
        outcome = _naive_outcome(metricledger_service, scenario)
        elapsed = (time.perf_counter() - start) * 1000
        outcome = _with_elapsed(outcome, elapsed)
        results[NAIVE].append(_scenario_result(scenario, outcome))

    category_counts: dict[str, int] = {}
    for scenario in suite.scenarios:
        category_counts[scenario.category] = (
            category_counts.get(scenario.category, 0) + 1
        )

    baselines = {
        name: _aggregate(name, tuple(outcomes))
        for name, outcomes in results.items()
    }
    snapshot_row_count = _snapshot_row_count(database_path)
    environment = {
        "python_version": platform.python_version(),
        "duckdb_version": duckdb.__version__,
        "snapshot_row_count": snapshot_row_count,
        "snapshot_ids": ",".join(
            record.snapshot_id for record in snapshots.records
        ),
        "semantic_bundle": f"{registry.bundle.bundle_id}@{registry.bundle.version}",
        "semantic_hash": registry.bundle.canonical_hash,
    }
    return EvaluationReport(
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        commit=commit,
        worktree_dirty=worktree_dirty,
        source_path=_repository_relative_path(suite.source_path),
        scenario_count=len(suite.scenarios),
        category_counts=category_counts,
        baselines=baselines,
        environment=environment,
        results={
            name: tuple(outcomes) for name, outcomes in results.items()
        },
    )


def _service(
    database_path: str | Path,
    registry: SemanticRegistry,
    snapshots: SnapshotRepository,
) -> AnalysisService:
    engine = DuckDbEngine(
        database_path,
        {
            record.snapshot_id: record.table_name
            for record in snapshots.records
        },
    )
    return AnalysisService(
        registry=registry,
        snapshots=snapshots,
        engine=engine,
    )


def _metricledger_outcome(
    service: AnalysisService,
    scenario: Scenario,
    *,
    budget: BudgetPolicy,
) -> EvaluationOutcome:
    response = service.analyze(
        AnalysisRequest(
            question=scenario.question,
            as_of=scenario.as_of,
            principal=scenario.principal,
            budget=budget,
        )
    )
    return _response_outcome(response)


def _naive_outcome(
    service: AnalysisService,
    scenario: Scenario,
) -> EvaluationOutcome:
    full_principal = Principal(
        principal_id="naive-unrestricted",
        allowed_values={
            "region": ["east", "west", "north", "south"],
            "channel": ["app", "web", "mini_program"],
            "category": ["electronics", "beauty", "home", "fashion"],
        },
    )
    response = service.analyze(
        AnalysisRequest(
            question=scenario.question,
            as_of=scenario.as_of,
            principal=full_principal,
            budget=BudgetPolicy(max_queries=4),
        )
    )
    if (
        response.admission.status is AdmissionStatus.ADMITTED
        and response.answer
        and response.claims
    ):
        return _response_outcome(response)
    return EvaluationOutcome(
        status=AdmissionStatus.ADMITTED,
        completeness=Completeness.COMPLETE,
        answer="结果为 0。",
        claim_count=1,
        supported_claim_count=0,
        evidence_bound_claim_count=0,
        claim_types=("UNSUPPORTED_ZERO",),
        zero_filled=True,
        queries_used=0,
        elapsed_ms=0,
    )


def _response_outcome(response: AnalysisResponse) -> EvaluationOutcome:
    evidence_ids = {artifact.result_id for artifact in response.evidence}
    supported = 0
    evidence_bound = 0
    claim_types: list[str] = []
    for validated in response.claims:
        claim = validated.claim
        claim_types.append(claim.claim_type.value)
        if claim.support is ClaimSupport.SUPPORTED:
            supported += 1
        if (
            claim.evidence_refs
            and set(claim.evidence_refs).issubset(evidence_ids)
            and "{evidence:" not in validated.rendered_text
            and validated.rendered_text
        ):
            evidence_bound += 1
    return EvaluationOutcome(
        status=response.admission.status,
        completeness=response.completeness,
        answer=response.answer,
        claim_count=len(response.claims),
        supported_claim_count=supported,
        evidence_bound_claim_count=evidence_bound,
        claim_types=tuple(claim_types),
        zero_filled=False,
        queries_used=response.budget_usage.queries_used,
        elapsed_ms=float(response.budget_usage.elapsed_ms),
    )


def _with_elapsed(
    outcome: EvaluationOutcome,
    elapsed_ms: float,
) -> EvaluationOutcome:
    return EvaluationOutcome(
        status=outcome.status,
        completeness=outcome.completeness,
        answer=outcome.answer,
        claim_count=outcome.claim_count,
        supported_claim_count=outcome.supported_claim_count,
        evidence_bound_claim_count=outcome.evidence_bound_claim_count,
        claim_types=outcome.claim_types,
        zero_filled=outcome.zero_filled,
        queries_used=outcome.queries_used,
        elapsed_ms=round(elapsed_ms, 3),
    )


def _scenario_result(
    scenario: Scenario,
    outcome: EvaluationOutcome,
) -> ScenarioResult:
    status_matches = outcome.status is scenario.expected_status
    completeness_matches = (
        outcome.completeness is scenario.expected_completeness
    )
    has_investigation_claim = any(
        claim_type in {"CONTRIBUTION", "DIAGNOSTIC"}
        for claim_type in outcome.claim_types
    )
    evidence_requirement = (
        (
            has_investigation_claim
            if scenario.requires_investigation
            else outcome.claim_count >= 1
        )
        if scenario.expected_status is AdmissionStatus.ADMITTED
        else True
    )
    non_admitted_is_empty = (
        scenario.expected_status is AdmissionStatus.ADMITTED
        or (outcome.answer is None and outcome.claim_count == 0)
    )
    scenario_success = (
        status_matches
        and completeness_matches
        and evidence_requirement
        and non_admitted_is_empty
    )
    answer_present = bool(outcome.answer) or outcome.claim_count > 0
    unsafe_answer = (
        scenario.expected_status is not AdmissionStatus.ADMITTED
        and answer_present
    )
    scope_violation = (
        scenario.expected_status is AdmissionStatus.REFUSED
        and answer_present
    )
    return ScenarioResult(
        scenario_id=scenario.scenario_id,
        category=scenario.category,
        expected_status=scenario.expected_status.value,
        status_matches=status_matches,
        scenario_success=scenario_success,
        unsafe_answer=unsafe_answer,
        scope_violation=scope_violation,
        zero_as_empty=outcome.zero_filled,
        unsupported_claim_count=max(
            0, outcome.claim_count - outcome.supported_claim_count
        ),
        evidence_violation_count=max(
            0, outcome.claim_count - outcome.evidence_bound_claim_count
        ),
        budget_violation=outcome.queries_used > 4,
        outcome=outcome,
    )


def _aggregate(
    baseline: str,
    results: tuple[ScenarioResult, ...],
) -> BaselineMetrics:
    latencies = sorted(result.outcome.elapsed_ms for result in results)
    supported_investigations = [
        result
        for result in results
        if result.category == "supported_investigation"
    ]
    completed = [
        result
        for result in supported_investigations
        if result.scenario_success
        and result.outcome.completeness is Completeness.COMPLETE
    ]
    return BaselineMetrics(
        baseline=baseline,
        scenario_count=len(results),
        status_accuracy_count=sum(result.status_matches for result in results),
        scenario_success_count=sum(result.scenario_success for result in results),
        unsafe_answer_count=sum(result.unsafe_answer for result in results),
        scope_violation_count=sum(
            result.scope_violation for result in results
        ),
        zero_as_empty_count=sum(result.zero_as_empty for result in results),
        unsupported_claim_count=sum(
            result.unsupported_claim_count for result in results
        ),
        evidence_violation_count=sum(
            result.evidence_violation_count for result in results
        ),
        budget_violation_count=sum(
            result.budget_violation for result in results
        ),
        supported_investigation_count=len(supported_investigations),
        completed_supported_investigation_count=len(completed),
        total_claim_count=sum(
            result.outcome.claim_count for result in results
        ),
        evidence_bound_claim_count=sum(
            result.outcome.evidence_bound_claim_count for result in results
        ),
        elapsed_p50_ms=_percentile(latencies, 0.50),
        elapsed_p95_ms=_percentile(latencies, 0.95),
        total_queries=sum(
            result.outcome.queries_used for result in results
        ),
    )


def _percentile(values: Iterable[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return round(float(ordered[index]), 3)


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _snapshot_row_count(database_path: str | Path) -> int:
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM sales_batch_20261123_2300"
            ).fetchone()[0]
        )


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _repository_relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.name
