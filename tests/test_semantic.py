import pytest

from metricledger.semantic import SemanticRegistry


def test_metric_alias_can_be_ambiguous(registry: SemanticRegistry):
    metrics = registry.resolve_metric("成交")
    assert [metric.metric_id for metric in metrics] == [
        "paid_gmv",
        "paid_conversion_rate",
    ]


def test_semantic_bundle_hash_ignores_packaging_only_changes():
    first = registry_data()
    second = registry_data()
    second["version"] = "1.0.1"

    assert SemanticRegistry.from_mapping(first).bundle.canonical_hash != (
        SemanticRegistry.from_mapping(second).bundle.canonical_hash
    )


def test_unknown_dimension_value_is_rejected(registry: SemanticRegistry):
    with pytest.raises(ValueError, match="unknown value"):
        registry.validate_filter("region", ["moon"])


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
                "values": ["east", "west"],
            }
        ],
        "metrics": [
            {
                "metric_id": "paid_gmv",
                "display_name": "支付 GMV",
                "kind": "ADDITIVE",
                "expression": "SUM(f.paid_gmv)",
                "time_column": "event_date",
                "allowed_dimensions": ["region"],
                "aliases": ["成交", "GMV"],
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
                "allowed_dimensions": ["region"],
                "aliases": ["成交", "转化率"],
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
def registry() -> SemanticRegistry:
    return SemanticRegistry.from_mapping(registry_data())

