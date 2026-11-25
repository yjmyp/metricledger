from __future__ import annotations

import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import sqlglot
from sqlglot import exp

from .models import CompiledQuery, QueryRows


class UnknownSnapshotError(ValueError):
    """Raised when a snapshot id is not part of the fixed catalog."""


class QueryExecutionError(RuntimeError):
    """Raised when a validated query cannot be executed."""


class DuckDbEngine:
    def __init__(self, database_path: str | Path, snapshot_tables: dict[str, str]):
        self.database_path = Path(database_path)
        self.snapshot_tables = dict(snapshot_tables)
        self.execution_count = 0

    def execute(self, compiled: CompiledQuery, snapshot_id: str) -> QueryRows:
        expected_table = self.snapshot_tables.get(snapshot_id)
        if expected_table is None:
            raise UnknownSnapshotError(f"unknown snapshot: {snapshot_id}")
        if compiled.referenced_tables != [expected_table]:
            raise ValueError("compiled SQL does not match the fixed snapshot")
        self._assert_single_table(compiled.sql, expected_table)

        self.execution_count += 1
        started = time.perf_counter()
        try:
            with duckdb.connect(str(self.database_path), read_only=True) as connection:
                cursor = connection.execute(compiled.sql, compiled.parameters)
                columns = [description[0] for description in cursor.description]
                raw_rows = cursor.fetchmany(200)
        except Exception as exc:
            raise QueryExecutionError(f"query execution failed: {exc}") from exc

        rows = [
            {
                column: _json_value(value)
                for column, value in zip(columns, values, strict=True)
            }
            for values in raw_rows
        ]
        return QueryRows(
            rows=rows,
            row_count=len(rows),
            elapsed_ms=max(0, round((time.perf_counter() - started) * 1000)),
        )

    @staticmethod
    def _assert_single_table(sql: str, expected_table: str) -> None:
        try:
            statements = sqlglot.parse(sql, read="duckdb")
        except Exception as exc:
            raise ValueError("compiled SQL could not be parsed") from exc
        if len(statements) != 1 or not isinstance(statements[0], exp.Select):
            raise ValueError("compiled SQL must be a single SELECT")
        tables = {
            table.name for table in statements[0].find_all(exp.Table) if table.name
        }
        if tables != {expected_table}:
            raise ValueError("compiled SQL references a table outside the fixed snapshot")


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
