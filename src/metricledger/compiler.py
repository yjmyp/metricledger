from __future__ import annotations

from .guards import validate_select
from .models import (
    AnalysisIntent,
    CompiledQuery,
    MetricDefinition,
    Scope,
    SnapshotRecord,
    canonical_hash,
)
from .semantic import SemanticRegistry


class QueryCompiler:
    def __init__(self, registry: SemanticRegistry):
        self.registry = registry

    def compile(
        self,
        intent: AnalysisIntent,
        metric: MetricDefinition,
        snapshot: SnapshotRecord,
        scope: Scope,
        *,
        group_by: str | None = None,
        include_ratio_components: bool = False,
    ) -> CompiledQuery:
        if metric.metric_id != intent.metric_id:
            raise ValueError("intent metric does not match semantic metric")
        if group_by is not None and group_by not in metric.allowed_dimensions:
            raise ValueError("group-by dimension is not allowed for metric")

        parameters: list[str | int | float] = [
            intent.period.start.isoformat(),
            intent.period.end.isoformat(),
        ]
        selected = [f"{metric.expression} AS metric_value"]
        if include_ratio_components:
            if metric.kind.value != "RATIO":
                raise ValueError("ratio components are only available for ratio metrics")
            selected.extend(
                [
                    f"{metric.numerator} AS numerator_value",
                    f"{metric.denominator} AS denominator_value",
                ]
            )
        group_column = None
        if group_by is not None:
            dimension = self.registry.dimension(group_by)
            group_column = dimension.column
            selected.insert(0, f"f.{group_column} AS dimension_value")

        where = [
            f"f.{metric.time_column} BETWEEN ? AND ?",
        ]
        for dimension_id in metric.allowed_dimensions:
            dimension = self.registry.dimension(dimension_id)
            requested = intent.filters.get(dimension_id)
            values = requested or scope.values.get(dimension_id, [])
            self._validate_scope_values(dimension_id, requested, values, scope)
            if not values:
                raise ValueError(f"no authorized values for {dimension_id}")
            parameters.extend(values)
            placeholders = ", ".join("?" for _ in values)
            where.append(f"f.{dimension.column} IN ({placeholders})")
        parameters.append(scope.tenant_id)
        where.append("f.tenant_id = ?")

        sql = (
            f"SELECT {', '.join(selected)} "
            f"FROM {snapshot.table_name} AS f "
            f"WHERE {' AND '.join(where)}"
        )
        if group_column is not None:
            sql += f" GROUP BY f.{group_column}"

        validate_select(
            sql,
            allowed_tables={snapshot.table_name},
            allowed_columns={
                *self.registry.bundle.allowed_columns,
                "tenant_id",
                "metric_value",
                "numerator_value",
                "denominator_value",
                "dimension_value",
            },
            required_tables={snapshot.table_name},
            required_columns={
                self.registry.dimension(dimension_id).column
                for dimension_id in metric.allowed_dimensions
            }
            | {metric.time_column, "tenant_id"},
        )
        return CompiledQuery(
            sql=sql,
            parameters=parameters,
            fingerprint=canonical_hash({"sql": sql, "parameters": parameters}),
            referenced_tables=[snapshot.table_name],
            group_by=group_by,
        )

    def _validate_scope_values(
        self,
        dimension_id: str,
        requested: list[str] | None,
        values: list[str],
        scope: Scope,
    ) -> None:
        authorized = set(scope.values.get(dimension_id, []))
        self.registry.validate_filter(dimension_id, values)
        if not set(values).issubset(authorized):
            raise ValueError(f"requested values for {dimension_id} are not authorized")
        if requested is not None and not set(requested).issubset(authorized):
            raise ValueError(f"filter values for {dimension_id} are not authorized")
