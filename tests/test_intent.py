from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from metricledger.intent import RuleBasedIntentParser
from metricledger.models import AdmissionStatus, QuestionKind
from metricledger.semantic import SemanticRegistry


TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 11, 23, 23, 59, 59, tzinfo=TZ)


def registry_data() -> dict:
    return {
        "bundle_id": "ecommerce",
        "version": "1.0.0",
        "timezone": "Asia/Shanghai",
        "allowed_tables": ["sales_batch_20261123_2300"],
        "allowed_columns": [
            "event_date",
            "region",
            "channel",
            "category",
            "paid_orders",
            "sessions",
            "canceled_orders",
            "paid_gmv",
        ],
        "dimensions": [
            {
                "dimension_id": "region",
                "display_name": "区域",
                "column": "region",
                "values": ["east", "west", "north", "south"],
            },
            {
                "dimension_id": "channel",
                "display_name": "渠道",
                "column": "channel",
                "values": ["app", "web"],
            },
        ],
        "metrics": [
            {
                "metric_id": "paid_gmv",
                "display_name": "支付 GMV",
                "kind": "ADDITIVE",
                "expression": "SUM(f.paid_gmv)",
                "time_column": "event_date",
                "allowed_dimensions": ["region", "channel"],
                "aliases": ["支付GMV", "成交额", "成交", "GMV"],
                "freshness_policy_id": "daily",
                "quality_policy_id": "strict",
            },
            {
                "metric_id": "paid_conversion_rate",
                "display_name": "支付转化率",
                "kind": "RATIO",
                "expression": "CAST(SUM(f.paid_orders) AS DOUBLE) / NULLIF(SUM(f.sessions), 0)",
                "numerator": "SUM(f.paid_orders)",
                "denominator": "SUM(f.sessions)",
                "time_column": "event_date",
                "allowed_dimensions": ["region", "channel"],
                "aliases": ["支付转化率", "转化率", "成交"],
                "freshness_policy_id": "daily",
                "quality_policy_id": "strict",
            },
            {
                "metric_id": "cancel_rate",
                "display_name": "订单取消率",
                "kind": "RATIO",
                "expression": "CAST(SUM(f.canceled_orders) AS DOUBLE) / NULLIF(SUM(f.paid_orders), 0)",
                "numerator": "SUM(f.canceled_orders)",
                "denominator": "SUM(f.paid_orders)",
                "time_column": "event_date",
                "allowed_dimensions": ["region", "channel"],
                "aliases": ["订单取消率", "取消率"],
                "freshness_policy_id": "daily",
                "quality_policy_id": "strict",
            },
        ],
        "freshness_policies": [
            {
                "policy_id": "daily",
                "max_lag_hours": 24,
                "zero_fill_missing_groups": False,
            }
        ],
        "quality_policies": [
            {
                "policy_id": "strict",
                "max_null_rate": 0.001,
                "max_duplicate_rate": 0.001,
                "require_positive_denominator": True,
            }
        ],
    }


@pytest.fixture
def parser() -> RuleBasedIntentParser:
    return RuleBasedIntentParser(SemanticRegistry.from_mapping(registry_data()))


def test_parser_marks_two_metric_aliases_as_ambiguous(parser: RuleBasedIntentParser):
    parsed = parser.parse(
        "2026-11-17 到 2026-11-23 成交和取消率为什么变化",
        as_of=AS_OF,
    )

    assert parsed.status is AdmissionStatus.CLARIFY
    assert parsed.issue_codes == ["AMBIGUOUS_METRIC"]
    assert parsed.intent is None


def test_parser_builds_intent_and_equal_length_comparison_period(
    parser: RuleBasedIntentParser,
):
    parsed = parser.parse(
        "2026-11-17 到 2026-11-23 为什么支付 GMV 下降？",
        as_of=AS_OF,
    )

    assert parsed.status is AdmissionStatus.ADMITTED
    assert parsed.intent is not None
    assert parsed.intent.metric_id == "paid_gmv"
    assert parsed.intent.question_kind is QuestionKind.INVESTIGATE
    assert parsed.intent.period.start == date(2026, 11, 17)
    assert parsed.intent.period.end == date(2026, 11, 23)
    assert parsed.intent.comparison_period is not None
    assert parsed.intent.comparison_period.start == date(2026, 11, 10)
    assert parsed.intent.comparison_period.end == date(2026, 11, 16)


def test_parser_collects_only_semantic_candidate_dimensions(
    parser: RuleBasedIntentParser,
):
    parsed = parser.parse(
        "2026-11-17 到 2026-11-23 按区域看支付 GMV 为什么下降",
        as_of=AS_OF,
    )

    assert parsed.intent is not None
    assert parsed.intent.candidate_dimensions == ["region"]


def test_parser_requires_an_explicit_period(parser: RuleBasedIntentParser):
    parsed = parser.parse("帮我看支付 GMV 为什么下降", as_of=AS_OF)

    assert parsed.status is AdmissionStatus.CLARIFY
    assert parsed.issue_codes == ["MISSING_PERIOD"]
    assert parsed.intent is None
