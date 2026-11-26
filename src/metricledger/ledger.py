from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from threading import Lock

from .models import AnalysisResponse


class SqliteLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.write_count = 0
        self.write_error_count = 0
        self.total_write_ms = 0.0
        self.read_count = 0
        self.read_error_count = 0
        self.total_read_ms = 0.0
        self._metrics_lock = Lock()
        self._initialize()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analyses (
                    analysis_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT 'demo',
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS result_artifacts (
                    result_id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL DEFAULT 'demo',
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_edges (
                    edge_id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL DEFAULT 'demo',
                    source_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    target_id TEXT NOT NULL
                )
                """
            )
            self._ensure_tenant_columns(connection)

    @staticmethod
    def _ensure_tenant_columns(connection: sqlite3.Connection) -> None:
        for table in ("analyses", "result_artifacts", "graph_edges"):
            columns = {
                row[1]
                for row in connection.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            }
            if "tenant_id" not in columns:
                connection.execute(
                    f"ALTER TABLE {table} "
                    "ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'demo'"
                )

    def save(self, response: AnalysisResponse) -> None:
        started = time.perf_counter()
        try:
            self._save_impl(response)
        except Exception:
            self._record_write((time.perf_counter() - started) * 1000, error=True)
            raise
        self._record_write((time.perf_counter() - started) * 1000, error=False)

    def _save_impl(self, response: AnalysisResponse) -> None:
        payload = _canonical_json(response.model_dump(mode="json"))
        tenant_id = (
            response.context.tenant_id
            if response.context is not None
            else "local"
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._insert_immutable(
                    connection,
                    table="analyses",
                    key_column="analysis_id",
                    key_value=response.analysis_id,
                    payload=payload,
                    insert_columns=(
                        "analysis_id",
                        "tenant_id",
                        "payload",
                    ),
                    insert_values=(
                        response.analysis_id,
                        tenant_id,
                        payload,
                    ),
                    message="analysis ledger entries are immutable",
                )
                for artifact in response.evidence:
                    artifact_payload = _canonical_json(
                        artifact.model_dump(mode="json")
                    )
                    self._insert_immutable(
                        connection,
                        table="result_artifacts",
                        key_column="result_id",
                        key_value=artifact.result_id,
                        payload=artifact_payload,
                        insert_columns=(
                            "result_id",
                            "analysis_id",
                            "tenant_id",
                            "payload",
                        ),
                        insert_values=(
                            artifact.result_id,
                            response.analysis_id,
                            tenant_id,
                            artifact_payload,
                        ),
                        message="result artifacts are immutable",
                    )
                for claim in response.claims:
                    for result_id in claim.claim.evidence_refs:
                        connection.execute(
                            """
                            INSERT INTO graph_edges(
                                edge_id, analysis_id, tenant_id,
                                source_id, relation, target_id
                            )
                            VALUES (?, ?, ?, ?, 'SUPPORTED_BY', ?)
                            ON CONFLICT(edge_id) DO NOTHING
                            """,
                            (
                                f"{claim.claim.claim_id}:{result_id}",
                                response.analysis_id,
                                tenant_id,
                                claim.claim.claim_id,
                                result_id,
                            ),
                        )
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _insert_immutable(
        connection: sqlite3.Connection,
        *,
        table: str,
        key_column: str,
        key_value: str,
        payload: str,
        insert_columns: tuple[str, ...],
        insert_values: tuple[object, ...],
        message: str,
    ) -> None:
        placeholders = ", ".join("?" for _ in insert_columns)
        columns = ", ".join(insert_columns)
        try:
            connection.execute(
                f"INSERT INTO {table}({columns}) VALUES ({placeholders})",
                insert_values,
            )
        except sqlite3.IntegrityError:
            existing = connection.execute(
                f"SELECT payload FROM {table} WHERE {key_column} = ?",
                (key_value,),
            ).fetchone()
            if existing is None or existing[0] != payload:
                raise ValueError(message) from None

    def get(
        self,
        analysis_id: str,
        *,
        tenant_id: str | None = None,
    ) -> AnalysisResponse | None:
        started = time.perf_counter()
        try:
            response = self._get_impl(analysis_id, tenant_id=tenant_id)
        except Exception:
            self._record_read((time.perf_counter() - started) * 1000, error=True)
            raise
        self._record_read((time.perf_counter() - started) * 1000, error=False)
        return response

    def _get_impl(
        self,
        analysis_id: str,
        *,
        tenant_id: str | None = None,
    ) -> AnalysisResponse | None:
        with self._connect() as connection:
            if tenant_id is None:
                row = connection.execute(
                    "SELECT payload FROM analyses WHERE analysis_id = ?",
                    (analysis_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    (
                        "SELECT payload FROM analyses "
                        "WHERE analysis_id = ? AND tenant_id = ?"
                    ),
                    (analysis_id, tenant_id),
                ).fetchone()
        if row is None:
            return None
        return AnalysisResponse.model_validate_json(row[0])

    def _record_write(self, elapsed_ms: float, *, error: bool) -> None:
        with self._metrics_lock:
            if error:
                self.write_error_count += 1
            else:
                self.write_count += 1
            self.total_write_ms += max(0.0, elapsed_ms)

    def _record_read(self, elapsed_ms: float, *, error: bool) -> None:
        with self._metrics_lock:
            if error:
                self.read_error_count += 1
            else:
                self.read_count += 1
            self.total_read_ms += max(0.0, elapsed_ms)

    def edge_count(
        self,
        analysis_id: str,
        *,
        tenant_id: str | None = None,
    ) -> int:
        with self._connect() as connection:
            if tenant_id is None:
                row = connection.execute(
                    "SELECT count(*) FROM graph_edges WHERE analysis_id = ?",
                    (analysis_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    (
                        "SELECT count(*) FROM graph_edges "
                        "WHERE analysis_id = ? AND tenant_id = ?"
                    ),
                    (analysis_id, tenant_id),
                ).fetchone()
        return int(row[0])

    def dump_all_text(self) -> str:
        with self._connect() as connection:
            return "\n".join(connection.iterdump())

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path))
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
