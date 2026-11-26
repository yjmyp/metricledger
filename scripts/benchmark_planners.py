from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(PROJECT_ROOT))

from metricledger.evaluation import load_suite
from metricledger.models import (
    DimensionDefinition,
    MetricKind,
    ObservationSummary,
    QuestionKind,
)
from metricledger.planners import (
    HeuristicHypothesisPlanner,
    OpenAICompatibleHypothesisPlanner,
)
from metricledger.semantic import SemanticRegistry


def run_planner_benchmark(
    *,
    output_json: str | Path,
    output_markdown: str | Path,
    model_client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Compare deterministic and model-assisted planners on fixed scenarios."""

    registry = SemanticRegistry.load_default()
    suite = load_suite()
    scenarios = [
        scenario
        for scenario in suite.scenarios
        if scenario.category == "supported_investigation"
    ]
    if len(scenarios) != 15:
        raise ValueError(
            "planner benchmark expects exactly 15 supported investigation cases"
        )

    cases = [
        _case_for_question(
            scenario.question,
            scenario_id=scenario.scenario_id,
            registry=registry,
        )
        for scenario in scenarios
    ]
    heuristic = HeuristicHypothesisPlanner()
    heuristic_report = _run_planner(
        heuristic,
        cases,
        planner_name="heuristic",
        include_trace=False,
    )

    base_url = os.environ.get("METRICLEDGER_PLANNER_BASE_URL", "").strip()
    model_name = os.environ.get("METRICLEDGER_PLANNER_MODEL", "").strip()
    api_key = os.environ.get("METRICLEDGER_PLANNER_API_KEY", "").strip() or None
    credentials_configured = bool(base_url and model_name)
    if model_client is not None and not credentials_configured:
        base_url = "http://injected-model.invalid/v1"
        model_name = "injected-model"
        credentials_configured = True

    if credentials_configured:
        if not base_url or not model_name:
            raise ValueError(
                "METRICLEDGER_PLANNER_BASE_URL and "
                "METRICLEDGER_PLANNER_MODEL must be configured together"
            )
        planner = OpenAICompatibleHypothesisPlanner(
            base_url=base_url,
            model=model_name,
            api_key=api_key,
            client=model_client,
        )
        model_report = _run_planner(
            planner,
            cases,
            planner_name="openai_compatible",
            include_trace=True,
        )
        model_report["credentials_configured"] = True
        model_report["model"] = _safe_model_label(base_url, model_name)
    else:
        model_report = {
            "status": "NOT_RUN",
            "credentials_configured": False,
            "case_count": len(cases),
            "reason": (
                "No METRICLEDGER_PLANNER_BASE_URL and "
                "METRICLEDGER_PLANNER_MODEL were configured. "
                "No model latency or quality can be claimed."
            ),
            "latency_ms": {},
            "failure_rate": None,
            "fallback_rate": None,
            "outcome_counts": {},
            "fallback_reason_counts": {},
            "usage_complete_count": 0,
            "planner_tokens": 0,
        }

    report = {
        "status": "COMPLETE",
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "scope": "15 deterministic supported_investigation scenarios",
        "case_count": len(cases),
        "planner_inputs": {
            "candidate_dimensions_only": True,
            "raw_event_rows_exposed": False,
            "max_hypotheses": 3,
        },
        "heuristic": heuristic_report,
        "model": model_report,
        "environment": {
            "python": platform.python_version(),
            "httpx": httpx.__version__,
        },
        "caveat": (
            "The heuristic comparison is a real in-process measurement on "
            "synthetic scenarios. The model comparison is RUN only when "
            "credentials or an injected client are explicitly configured."
        ),
    }

    output_json_path = Path(output_json)
    output_markdown_path = Path(output_markdown)
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    output_markdown_path.write_text(
        _render_markdown(report),
        encoding="utf-8",
    )
    return report


def _case_for_question(
    question: str,
    *,
    scenario_id: str,
    registry: SemanticRegistry,
) -> tuple[ObservationSummary, list[DimensionDefinition]]:
    dimensions = list(registry.bundle.dimensions)
    by_name = {dimension.display_name: dimension for dimension in dimensions}
    candidate_ids = [
        dimension.dimension_id
        for name, dimension in by_name.items()
        if name in question
    ]
    if not candidate_ids:
        candidate_ids = [dimension.dimension_id for dimension in dimensions]

    metric_id = "paid_gmv"
    metric_kind = MetricKind.ADDITIVE
    if "支付转化率" in question:
        metric_id = "paid_conversion_rate"
        metric_kind = MetricKind.RATIO
    elif "订单取消率" in question:
        metric_id = "cancel_rate"
        metric_kind = MetricKind.RATIO

    observation = ObservationSummary(
        analysis_id=f"planner-{scenario_id}",
        metric_id=metric_id,
        metric_kind=metric_kind,
        question_kind=QuestionKind.INVESTIGATE,
        candidate_dimensions=candidate_ids,
        remaining_queries=6,
        remaining_planner_tokens=2_000,
        evidence_summaries=[],
    )
    return observation, dimensions


def _run_planner(
    planner,
    cases: list[tuple[ObservationSummary, list[DimensionDefinition]]],
    *,
    planner_name: str,
    include_trace: bool,
) -> dict[str, Any]:
    latencies: list[float] = []
    failure_count = 0
    fallback_count = 0
    outcome_counts: dict[str, int] = {}
    fallback_reasons: dict[str, int] = {}
    usage_complete_count = 0
    planner_tokens = 0
    results: list[dict[str, Any]] = []
    for observation, dimensions in cases:
        started = time.perf_counter()
        try:
            decision = planner.propose(
                observation,
                dimensions,
                remaining_queries=6,
                round_number=1,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            hypotheses = decision.hypotheses
            failure = not hypotheses
        except Exception as exc:  # pragma: no cover - defensive benchmark path
            elapsed_ms = (time.perf_counter() - started) * 1000
            hypotheses = []
            failure = True
            decision = None
            results.append(
                {
                    "analysis_id": observation.analysis_id,
                    "elapsed_ms": round(elapsed_ms, 3),
                    "hypothesis_count": 0,
                    "failure": True,
                    "error": type(exc).__name__,
                }
            )
        else:
            trace = decision.attempt
            outcome_counts[trace.outcome] = outcome_counts.get(trace.outcome, 0) + 1
            if trace.fallback_used:
                fallback_count += 1
                reason = trace.fallback_reason or "unknown"
                fallback_reasons[reason] = fallback_reasons.get(reason, 0) + 1
            if decision.usage is not None and trace.usage_complete:
                usage_complete_count += 1
                planner_tokens += decision.usage.total_tokens
            results.append(
                {
                    "analysis_id": observation.analysis_id,
                    "elapsed_ms": round(elapsed_ms, 3),
                    "hypothesis_count": len(hypotheses),
                    "failure": failure,
                    "outcome": trace.outcome if trace is not None else None,
                    "fallback_used": (
                        trace.fallback_used if trace is not None else False
                    ),
                    "usage_complete": trace.usage_complete,
                    "planner_tokens": (
                        decision.usage.total_tokens
                        if decision.usage is not None
                        else None
                    ),
                }
            )
        latencies.append(elapsed_ms)
        failure_count += int(failure)

    return {
        "status": "RUN",
        "planner": planner_name,
        "case_count": len(cases),
        "failure_count": failure_count,
        "failure_rate": round(failure_count / len(cases), 6),
        "fallback_count": fallback_count,
        "fallback_rate": round(fallback_count / len(cases), 6),
        "latency_ms": _latency_summary(latencies),
        "outcome_counts": outcome_counts,
        "fallback_reason_counts": fallback_reasons,
        "usage_complete_count": usage_complete_count,
        "planner_tokens": planner_tokens,
        "results": results,
        "trace_available": include_trace,
    }


def _latency_summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"min": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    ordered = sorted(values)
    return {
        "min": round(ordered[0], 3),
        "p50": round(statistics.median(ordered), 3),
        "p95": round(_percentile(ordered, 0.95), 3),
        "max": round(ordered[-1], 3),
    }


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    index = max(0, math.ceil(quantile * len(values)) - 1)
    return float(values[index])


def _safe_model_label(base_url: str, model: str) -> str:
    host = base_url.split("://", 1)[-1].split("/", 1)[0]
    return f"{host}/{model}"


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MetricLedger Planner Comparison",
        "",
        f"> 生成时间：`{report['generated_at']}`",
        "> 输入只包含指标观察摘要、允许维度和剩余查询预算；模型不会接触原始事件行。",
        "",
        "## 场景",
        "",
        f"- 支持调查场景：**{report['case_count']}**",
        f"- 候选维度暴露：`{report['planner_inputs']['candidate_dimensions_only']}`",
        f"- 原始事件行暴露：`{report['planner_inputs']['raw_event_rows_exposed']}`",
        "",
        "## 启发式 planner（本轮实测）",
        "",
        _metrics_table(report["heuristic"]),
        "",
        "## OpenAI-compatible planner",
        "",
    ]
    model = report["model"]
    if model["status"] == "NOT_RUN":
        lines.extend(
            [
                "**NOT_RUN**",
                "",
                f"原因：{model['reason']}",
                "",
                "因此本轮没有模型失败率、延迟或回退率结论。",
            ]
        )
    else:
        lines.extend(
            [
                f"- 模型标签：`{model.get('model', 'unknown')}`",
                f"- 凭证/端点已配置：`{model['credentials_configured']}`",
                f"- 失败率：`{model['failure_rate']:.2%}`",
                f"- 回退率：`{model['fallback_rate']:.2%}`",
                "",
                _metrics_table(model),
            ]
        )
    lines.extend(
        [
            "",
            "## 口径",
            "",
            "- 失败：planner 没有返回任何可清洗的候选维度。",
            "- 回退：模型调用异常、超时、HTTP 错误或输出无有效维度，系统使用启发式结果。",
            "- 场景为仓库内确定性合成场景，不代表外部榜单或生产 SLA。",
            "",
        ]
    )
    return "\n".join(lines)


def _metrics_table(metrics: dict[str, Any]) -> str:
    latency = metrics["latency_ms"]
    failure = metrics.get("failure_rate")
    fallback = metrics.get("fallback_rate")
    return "\n".join(
        [
            "| 指标 | 值 |",
            "|---|---:|",
            f"| 用例数 | {metrics['case_count']} |",
            f"| 失败率 | {_format_optional_rate(failure)} |",
            f"| 回退率 | {_format_optional_rate(fallback)} |",
            f"| P50 延迟 | {latency['p50']:.3f} ms |",
            f"| P95 延迟 | {latency['p95']:.3f} ms |",
            f"| 最大延迟 | {latency['max']:.3f} ms |",
            f"| 完整 Token 用量 | {metrics['usage_complete_count']} |",
            f"| Planner Token | {metrics['planner_tokens']} |",
        ]
    )


def _format_optional_rate(value: float | None) -> str:
    return "NOT_RUN" if value is None else f"{value:.2%}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare MetricLedger planners on fixed scenarios"
    )
    parser.add_argument(
        "--output-json",
        default="docs/verification/planner-results.json",
    )
    parser.add_argument(
        "--output-markdown",
        default="docs/verification/planner-report.md",
    )
    args = parser.parse_args()
    report = run_planner_benchmark(
        output_json=args.output_json,
        output_markdown=args.output_markdown,
    )
    print(f"heuristic: {report['heuristic']['case_count']} cases")
    print(f"model: {report['model']['status']}")
    print(f"json: {args.output_json}")
    print(f"markdown: {args.output_markdown}")


if __name__ == "__main__":
    main()
