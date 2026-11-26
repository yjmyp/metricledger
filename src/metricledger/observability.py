from __future__ import annotations

import math
import re
import secrets
import time
from collections import defaultdict, deque
from contextvars import ContextVar, Token
from dataclasses import dataclass
from threading import Lock

from .models import AnalysisResponse


_TRACEPARENT = re.compile(
    r"^(?P<version>[0-9a-fA-F]{2})-"
    r"(?P<trace_id>[0-9a-fA-F]{32})-"
    r"(?P<span_id>[0-9a-fA-F]{16})-"
    r"(?P<flags>[0-9a-fA-F]{2})$"
)


@dataclass(frozen=True)
class TraceContext:
    """W3C trace context for correlated structured logs."""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    sampled: bool = True

    @classmethod
    def from_traceparent(cls, value: str | None) -> TraceContext:
        parsed = _parse_traceparent(value)
        if parsed is None:
            return cls(
                trace_id=_new_trace_id(),
                span_id=_new_span_id(),
                parent_span_id=None,
            )
        trace_id, parent_span_id, flags = parsed
        return cls(
            trace_id=trace_id,
            span_id=_new_span_id(),
            parent_span_id=parent_span_id,
            sampled=bool(flags & 0x01),
        )

    @property
    def traceparent(self) -> str:
        flags = "01" if self.sampled else "00"
        return f"00-{self.trace_id}-{self.span_id}-{flags}"

    def child_span(self) -> TraceContext:
        return TraceContext(
            trace_id=self.trace_id,
            span_id=_new_span_id(),
            parent_span_id=self.span_id,
            sampled=self.sampled,
        )


_CURRENT_TRACE: ContextVar[TraceContext | None] = ContextVar(
    "metricledger_trace_context",
    default=None,
)


def set_trace_context(trace: TraceContext) -> Token:
    return _CURRENT_TRACE.set(trace)


def reset_trace_context(token: Token) -> None:
    _CURRENT_TRACE.reset(token)


def current_trace_context() -> TraceContext | None:
    return _CURRENT_TRACE.get()


def _parse_traceparent(
    value: str | None,
) -> tuple[str, str, int] | None:
    if not value:
        return None
    match = _TRACEPARENT.fullmatch(value.strip())
    if match is None or match.group("version") != "00":
        return None
    trace_id = match.group("trace_id").lower()
    span_id = match.group("span_id").lower()
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    return trace_id, span_id, int(match.group("flags"), 16)


def _new_trace_id() -> str:
    return secrets.token_hex(16)


def _new_span_id() -> str:
    while True:
        span_id = secrets.token_hex(8)
        if span_id != "0" * 16:
            return span_id


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


class InMemoryRateLimiter:
    """Single-process fixed-window limiter.

    This is intentionally local. Distributed deployments should replace it
    with a shared Redis/API-gateway policy.
    """

    def __init__(self, *, limit: int, window_seconds: float = 60.0):
        if limit < 1 or window_seconds <= 0:
            raise ValueError("rate limit and window must be positive")
        self.limit = limit
        self.window_seconds = float(window_seconds)
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> RateLimitDecision:
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets[key]
            cutoff = now - self.window_seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = max(
                    1,
                    math.ceil(bucket[0] + self.window_seconds - now),
                )
                return RateLimitDecision(False, retry_after)
            bucket.append(now)
        return RateLimitDecision(True)


class RequestMetrics:
    """Small Prometheus text-format registry with no external dependency."""

    HISTOGRAM_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)

    def __init__(self):
        self._lock = Lock()
        self._request_counts: dict[tuple[str, str, int], int] = defaultdict(int)
        self._duration_counts: dict[tuple[str, str], list[float]] = {}
        self._auth_failures = 0
        self._rate_limited = 0
        self._analysis_issue_counts: dict[
            tuple[str, str, str], int
        ] = defaultdict(int)
        self._planner_calls: dict[str, int] = defaultdict(int)
        self._planner_tokens = 0
        self._investigation_steps: dict[str, int] = defaultdict(int)
        self._context_drift = 0

    def record_request(
        self,
        *,
        method: str,
        path: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        with self._lock:
            self._request_counts[(method, path, status_code)] += 1
            buckets = self._duration_counts.setdefault(
                (method, path),
                [0.0] * (len(self.HISTOGRAM_BUCKETS) + 1),
            )
            for index, bucket in enumerate(self.HISTOGRAM_BUCKETS):
                if duration_seconds <= bucket:
                    buckets[index] += 1
                    break
            else:
                buckets[-1] += 1

    def record_auth_failure(self) -> None:
        with self._lock:
            self._auth_failures += 1

    def record_rate_limited(self) -> None:
        with self._lock:
            self._rate_limited += 1

    def record_analysis(self, response: AnalysisResponse) -> None:
        admission = response.admission.status.value
        with self._lock:
            for issue in response.issues:
                self._analysis_issue_counts[
                    (admission, issue.code, issue.severity.value)
                ] += 1
            investigation = response.investigation
            if investigation is not None:
                for round_trace in investigation.rounds:
                    self._planner_calls[round_trace.planner.outcome] += 1
                    for step in round_trace.steps:
                        self._investigation_steps[step.status.value] += 1
                if response.budget_usage.planner_usage_complete:
                    self._planner_tokens += (
                        response.budget_usage.planner_tokens_used or 0
                    )
                if investigation.stop_reason == "context_drift":
                    self._context_drift += 1

    def render(self, *, engine=None, ledger=None) -> str:
        with self._lock:
            request_counts = dict(self._request_counts)
            duration_counts = {
                key: list(value)
                for key, value in self._duration_counts.items()
            }
            auth_failures = self._auth_failures
            rate_limited = self._rate_limited
            analysis_issue_counts = dict(self._analysis_issue_counts)
            planner_calls = dict(self._planner_calls)
            planner_tokens = self._planner_tokens
            investigation_steps = dict(self._investigation_steps)
            context_drift = self._context_drift

        lines = [
            "# HELP metricledger_http_requests_total HTTP requests processed.",
            "# TYPE metricledger_http_requests_total counter",
        ]
        for (method, path, status_code), count in sorted(request_counts.items()):
            lines.append(
                "metricledger_http_requests_total"
                f'{{method="{_escape(method)}",path="{_escape(path)}",'
                f'status="{status_code}"}} {count}'
            )
        lines.extend(
            [
                "# HELP metricledger_http_request_duration_seconds "
                "Request latency histogram.",
                "# TYPE metricledger_http_request_duration_seconds histogram",
            ]
        )
        for (method, path), counts in sorted(duration_counts.items()):
            cumulative = 0
            for index, bucket in enumerate(self.HISTOGRAM_BUCKETS):
                cumulative += int(counts[index])
                lines.append(
                    "metricledger_http_request_duration_seconds_bucket"
                    f'{{method="{_escape(method)}",path="{_escape(path)}",'
                    f'le="{bucket}"}} {cumulative}'
                )
            cumulative += int(counts[-1])
            lines.append(
                "metricledger_http_request_duration_seconds_bucket"
                f'{{method="{_escape(method)}",path="{_escape(path)}",'
                f'le="+Inf"}} {cumulative}'
            )
            lines.append(
                "metricledger_http_request_duration_seconds_count"
                f'{{method="{_escape(method)}",path="{_escape(path)}"}} '
                f"{cumulative}"
            )
        lines.extend(
            [
                "# TYPE metricledger_auth_failures_total counter",
                f"metricledger_auth_failures_total {auth_failures}",
                "# TYPE metricledger_rate_limited_total counter",
                f"metricledger_rate_limited_total {rate_limited}",
                "# HELP metricledger_analysis_issues_total "
                "Analysis issues by admission status and issue code.",
                "# TYPE metricledger_analysis_issues_total counter",
            ]
        )
        for (admission, code, severity), count in sorted(
            analysis_issue_counts.items()
        ):
            lines.append(
                "metricledger_analysis_issues_total"
                f'{{admission="{_escape(admission)}",'
                f'code="{_escape(code)}",'
                f'severity="{_escape(severity)}"}} {count}'
            )
        lines.extend(
            [
                "# HELP metricledger_planner_calls_total "
                "Planner attempts by outcome.",
                "# TYPE metricledger_planner_calls_total counter",
            ]
        )
        for outcome, count in sorted(planner_calls.items()):
            lines.append(
                "metricledger_planner_calls_total"
                f'{{outcome="{_escape(outcome)}"}} {count}'
            )
        lines.extend(
            [
                "# HELP metricledger_planner_tokens_total "
                "Planner tokens from responses with complete usage.",
                "# TYPE metricledger_planner_tokens_total counter",
                f"metricledger_planner_tokens_total {planner_tokens}",
                "# HELP metricledger_investigation_steps_total "
                "Investigation evidence steps by status.",
                "# TYPE metricledger_investigation_steps_total counter",
            ]
        )
        for status, count in sorted(investigation_steps.items()):
            lines.append(
                "metricledger_investigation_steps_total"
                f'{{status="{_escape(status)}"}} {count}'
            )
        lines.extend(
            [
                "# HELP metricledger_context_drift_total "
                "Analyses stopped because admitted context drifted.",
                "# TYPE metricledger_context_drift_total counter",
                f"metricledger_context_drift_total {context_drift}",
            ]
        )
        if engine is not None:
            lines.extend(
                [
                    "# TYPE metricledger_duckdb_queries_total counter",
                    f"metricledger_duckdb_queries_total "
                    f"{engine.execution_count}",
                    "# TYPE metricledger_duckdb_query_errors_total counter",
                    f"metricledger_duckdb_query_errors_total "
                    f"{engine.query_error_count}",
                    "# TYPE metricledger_duckdb_query_time_seconds_total counter",
                    f"metricledger_duckdb_query_time_seconds_total "
                    f"{engine.total_query_ms / 1000:.6f}",
                ]
            )
        if ledger is not None:
            lines.extend(
                [
                    "# TYPE metricledger_sqlite_ledger_writes_total counter",
                    f"metricledger_sqlite_ledger_writes_total "
                    f"{ledger.write_count}",
                    "# TYPE metricledger_sqlite_ledger_write_errors_total counter",
                    f"metricledger_sqlite_ledger_write_errors_total "
                    f"{ledger.write_error_count}",
                    "# TYPE metricledger_sqlite_ledger_write_time_seconds_total counter",
                    f"metricledger_sqlite_ledger_write_time_seconds_total "
                    f"{ledger.total_write_ms / 1000:.6f}",
                    "# TYPE metricledger_sqlite_ledger_reads_total counter",
                    f"metricledger_sqlite_ledger_reads_total "
                    f"{ledger.read_count}",
                    "# TYPE metricledger_sqlite_ledger_read_errors_total counter",
                    f"metricledger_sqlite_ledger_read_errors_total "
                    f"{ledger.read_error_count}",
                    "# TYPE metricledger_sqlite_ledger_read_time_seconds_total counter",
                    f"metricledger_sqlite_ledger_read_time_seconds_total "
                    f"{ledger.total_read_ms / 1000:.6f}",
                ]
            )
        return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
