from __future__ import annotations

from collections.abc import Iterable

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


class SqlGuardError(ValueError):
    """Raised when generated SQL violates the read-only query contract."""


_ALLOWED_FUNCTIONS = {
    "ABS",
    "AVG",
    "CAST",
    "COALESCE",
    "COUNT",
    "MAX",
    "MIN",
    "NULLIF",
    "ROUND",
    "SUM",
}


def validate_select(
    sql: str,
    *,
    allowed_tables: Iterable[str],
    allowed_columns: Iterable[str],
    required_tables: Iterable[str] | None = None,
    required_columns: Iterable[str] | None = None,
) -> exp.Expression:
    """Validate one parameterized, read-only SELECT against an allowlist."""

    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except ParseError as exc:
        raise SqlGuardError(f"could not parse SQL: {exc}") from exc

    if len(statements) != 1:
        raise SqlGuardError("SQL must contain a single statement")
    statement = statements[0]
    if not isinstance(statement, exp.Select):
        raise SqlGuardError("SQL must be a read-only SELECT")
    if statement.args.get("with"):
        raise SqlGuardError("SQL must not contain a CTE")

    tables = _names(statement.find_all(exp.Table))
    table_allowlist = {value.lower() for value in allowed_tables}
    if not tables:
        raise SqlGuardError("SQL must reference an allowlisted table")
    unknown_tables = sorted(tables - table_allowlist)
    if unknown_tables:
        raise SqlGuardError(f"SQL references a table outside the allowlist: {unknown_tables[0]}")

    columns = _names(statement.find_all(exp.Column))
    column_allowlist = {value.lower() for value in allowed_columns}
    unknown_columns = sorted(columns - column_allowlist)
    if unknown_columns:
        raise SqlGuardError(
            f"SQL references a column outside the allowlist: {unknown_columns[0]}"
        )
    if any(isinstance(node, exp.Star) for node in statement.walk()):
        raise SqlGuardError("SQL must not select wildcard columns")

    for function in statement.find_all(exp.Anonymous):
        if function.name.upper() not in _ALLOWED_FUNCTIONS:
            raise SqlGuardError(f"function is not allowlisted: {function.name}")

    required_table_set = {value.lower() for value in required_tables or ()}
    if not required_table_set.issubset(tables):
        raise SqlGuardError("SQL does not reference the required fixed snapshot")
    required_column_set = {value.lower() for value in required_columns or ()}
    if not required_column_set.issubset(columns):
        raise SqlGuardError("SQL does not reference the required scoped columns")
    return statement


def _names(nodes) -> set[str]:
    return {node.name.lower() for node in nodes if node.name}
