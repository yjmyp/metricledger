from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_cli_answers_without_browser_or_api_server(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "ask.py"),
            "--database-path",
            str(tmp_path / "cli.duckdb"),
            "--ledger-path",
            str(tmp_path / "audit.sqlite3"),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )

    assert "进程内 CLI" in result.stdout
    assert "准入: ADMITTED" in result.stdout
    assert "732,400.00" in result.stdout
    assert "-9.18%" in result.stdout
    assert "Claim: 2" in result.stdout
    assert "查询: 4/4" in result.stdout
    assert "SQL 全为 SELECT: true" in result.stdout
