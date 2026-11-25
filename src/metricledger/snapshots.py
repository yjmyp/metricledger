from __future__ import annotations

from datetime import datetime

from .models import DateRange, SnapshotRecord


class SnapshotRepository:
    def __init__(self, records: list[SnapshotRecord]):
        self._records = sorted(records, key=lambda record: record.reporting_as_of)
        self._by_id = {record.snapshot_id: record for record in records}

    @classmethod
    def from_records(cls, records: list[dict]) -> SnapshotRepository:
        return cls([SnapshotRecord.model_validate(record) for record in records])

    @property
    def records(self) -> tuple[SnapshotRecord, ...]:
        return tuple(self._records)

    def get(self, snapshot_id: str) -> SnapshotRecord:
        try:
            return self._by_id[snapshot_id]
        except KeyError as exc:
            raise ValueError(f"unknown snapshot: {snapshot_id}") from exc

    def select(self, as_of: datetime, period: DateRange) -> SnapshotRecord | None:
        eligible = [
            record
            for record in self._records
            if record.complete
            and record.reporting_as_of <= as_of
            and record.period_start <= period.start
            and record.period_end >= period.end
        ]
        return eligible[-1] if eligible else None

    def latest_before(self, as_of: datetime) -> SnapshotRecord | None:
        eligible = [
            record
            for record in self._records
            if record.complete and record.reporting_as_of <= as_of
        ]
        return eligible[-1] if eligible else None

