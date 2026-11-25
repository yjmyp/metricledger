from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import AnalysisResponse


class SqliteLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analyses (
                    analysis_id TEXT PRIMARY KEY,
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
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_edges (
                    edge_id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    target_id TEXT NOT NULL
                )
                """
            )

    def save(self, response: AnalysisResponse) -> None:
        payload = _canonical_json(response.model_dump(mode="json"))
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT payload FROM analyses WHERE analysis_id = ?",
                (response.analysis_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] == payload:
                    return
                raise ValueError("analysis ledger entries are immutable")
            artifacts_to_insert = []
            for artifact in response.evidence:
                artifact_payload = _canonical_json(
                    artifact.model_dump(mode="json")
                )
                artifact_existing = connection.execute(
                    "SELECT payload FROM result_artifacts WHERE result_id = ?",
                    (artifact.result_id,),
                ).fetchone()
                if artifact_existing is None:
                    artifacts_to_insert.append(
                        (artifact, artifact_payload)
                    )
                elif artifact_existing[0] != artifact_payload:
                    raise ValueError("result artifacts are immutable")
            connection.execute(
                """
                INSERT INTO analyses(analysis_id, payload)
                VALUES (?, ?)
                """,
                (response.analysis_id, payload),
            )
            for artifact, artifact_payload in artifacts_to_insert:
                connection.execute(
                    """
                    INSERT INTO result_artifacts(result_id, analysis_id, payload)
                    VALUES (?, ?, ?)
                    """,
                    (
                        artifact.result_id,
                        response.analysis_id,
                        artifact_payload,
                    ),
                )
            for claim in response.claims:
                for result_id in claim.claim.evidence_refs:
                    edge_id = f"{claim.claim.claim_id}:{result_id}"
                    connection.execute(
                        """
                        INSERT INTO graph_edges(
                            edge_id, analysis_id, source_id, relation, target_id
                        )
                        VALUES (?, ?, ?, 'SUPPORTED_BY', ?)
                        ON CONFLICT(edge_id) DO NOTHING
                        """,
                        (
                            edge_id,
                            response.analysis_id,
                            claim.claim.claim_id,
                            result_id,
                        ),
                    )

    def get(self, analysis_id: str) -> AnalysisResponse | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM analyses WHERE analysis_id = ?",
                (analysis_id,),
            ).fetchone()
        if row is None:
            return None
        return AnalysisResponse.model_validate_json(row[0])

    def edge_count(self, analysis_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT count(*) FROM graph_edges WHERE analysis_id = ?",
                (analysis_id,),
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
