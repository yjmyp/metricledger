from __future__ import annotations

from .models import (
    DateRange,
    MetricDefinition,
    MetricKind,
    QualityCheck,
    QualityStatus,
    SnapshotRecord,
)
from .semantic import SemanticRegistry


class QualityService:
    def __init__(self, registry: SemanticRegistry):
        self.registry = registry

    def evaluate(
        self,
        metric: MetricDefinition,
        snapshot: SnapshotRecord,
        period: DateRange,
        *,
        denominator_positive: bool | None = None,
    ) -> list[QualityCheck]:
        policy = self.registry.quality_policies[metric.quality_policy_id]
        checks = [
            QualityCheck(
                code="SNAPSHOT_COMPLETE",
                status=(
                    QualityStatus.PASS if snapshot.complete else QualityStatus.BLOCK
                ),
                message="快照批次完整" if snapshot.complete else "快照批次不完整",
            ),
            QualityCheck(
                code="DATE_COVERAGE",
                status=(
                    QualityStatus.PASS
                    if snapshot.period_start <= period.start
                    and snapshot.period_end >= period.end
                    else QualityStatus.WARN
                ),
                message="快照覆盖请求周期"
                if snapshot.period_start <= period.start and snapshot.period_end >= period.end
                else "快照未完整覆盖请求周期",
            ),
            QualityCheck(
                code="DUPLICATE_RATE",
                status=(
                    QualityStatus.PASS
                    if snapshot.duplicate_rate <= policy.max_duplicate_rate
                    else QualityStatus.BLOCK
                ),
                message="业务主键重复率检查",
                value=snapshot.duplicate_rate,
            ),
            QualityCheck(
                code="NULL_RATE",
                status=(
                    QualityStatus.PASS
                    if snapshot.null_rate <= policy.max_null_rate
                    else QualityStatus.BLOCK
                ),
                message="关键字段非空率检查",
                value=snapshot.null_rate,
            ),
        ]
        if metric.kind is MetricKind.RATIO:
            if denominator_positive is False:
                status = QualityStatus.BLOCK
                message = "比率分母存在零值"
            elif denominator_positive is True:
                status = QualityStatus.PASS
                message = "比率分母为正"
            else:
                status = QualityStatus.WARN
                message = "尚未提供比率分母正值检查结果"
            checks.append(
                QualityCheck(
                    code="POSITIVE_DENOMINATOR",
                    status=status,
                    message=message,
                    value=denominator_positive,
                )
            )
        return checks
