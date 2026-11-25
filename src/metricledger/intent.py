from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from .models import (
    AdmissionStatus,
    AnalysisIntent,
    DateRange,
    IntentParse,
    QuestionKind,
)
from .semantic import SemanticRegistry


_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_INVESTIGATION_WORDS = ("为什么", "原因", "调查", "归因", "排查")
_CHANGE_WORDS = ("变化", "下降", "上升", "增长", "减少", "对比", "相比")


class RuleBasedIntentParser:
    """Turn a bounded question into a semantic intent.

    The parser is intentionally conservative: it never guesses a missing
    period or silently chooses between multiple metric aliases.
    """

    def __init__(self, registry: SemanticRegistry):
        self.registry = registry

    def parse(self, question: str, *, as_of: datetime) -> IntentParse:
        normalized = " ".join(question.strip().split())
        if not normalized:
            return self._clarify("EMPTY_QUESTION")
        if as_of.tzinfo is None:
            return self._clarify("INVALID_AS_OF")

        period_result = self._parse_period(normalized)
        if isinstance(period_result, IntentParse):
            return period_result
        period = period_result

        metrics = self.registry.resolve_metric(normalized)
        if not metrics:
            return self._clarify("MISSING_METRIC")
        if len(metrics) > 1:
            return self._clarify("AMBIGUOUS_METRIC")

        question_kind = self._question_kind(normalized)
        comparison_period = None
        if question_kind is not QuestionKind.VALUE:
            span = timedelta(days=period.day_count())
            comparison_period = DateRange(
                start=period.start - span,
                end=period.start - timedelta(days=1),
            )

        dimensions = [
            dimension.dimension_id
            for dimension in self.registry.bundle.dimensions
            if dimension.display_name in normalized
            or dimension.dimension_id.lower() in normalized.lower()
        ]
        intent = AnalysisIntent(
            metric_id=metrics[0].metric_id,
            question_kind=question_kind,
            period=period,
            comparison_period=comparison_period,
            candidate_dimensions=dimensions,
        )
        return IntentParse(status=AdmissionStatus.ADMITTED, intent=intent)

    def _parse_period(self, question: str) -> DateRange | IntentParse:
        raw_dates = _ISO_DATE.findall(question)
        if not raw_dates:
            return self._clarify("MISSING_PERIOD")
        if len(raw_dates) != 2:
            return self._clarify("AMBIGUOUS_PERIOD")
        try:
            start, end = (date.fromisoformat(value) for value in raw_dates)
        except ValueError:
            return self._clarify("INVALID_PERIOD")
        if end < start:
            return self._clarify("INVALID_PERIOD")
        return DateRange(start=start, end=end)

    def _question_kind(self, question: str) -> QuestionKind:
        if any(word in question for word in _INVESTIGATION_WORDS):
            return QuestionKind.INVESTIGATE
        if any(word in question for word in _CHANGE_WORDS):
            return QuestionKind.CHANGE
        return QuestionKind.VALUE

    @staticmethod
    def _clarify(code: str) -> IntentParse:
        return IntentParse(
            status=AdmissionStatus.CLARIFY,
            issue_codes=[code],
            message="问题缺少可安全解析的指标或时间范围",
        )
