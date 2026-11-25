from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from metricledger.evaluation import load_suite, run_evaluation
from metricledger.semantic import SemanticRegistry
from metricledger.snapshots import SnapshotRepository
from scripts.build_demo_data import DEMO_SNAPSHOTS, build_demo_database


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run deterministic MetricLedger baselines"
    )
    parser.add_argument(
        "--scenarios",
        default=str(PROJECT_ROOT / "eval" / "scenarios.json"),
        help="scenario catalog path",
    )
    parser.add_argument(
        "--output-json",
        default=str(PROJECT_ROOT / "eval" / "results.json"),
        help="machine-readable result path",
    )
    parser.add_argument(
        "--output-markdown",
        default=str(PROJECT_ROOT / "docs" / "verification" / "eval-report.md"),
        help="human-readable result path",
    )
    args = parser.parse_args()

    suite = load_suite(args.scenarios)
    registry = SemanticRegistry.load_default()
    snapshots = SnapshotRepository.from_records(DEMO_SNAPSHOTS)
    with tempfile.TemporaryDirectory(prefix="metricledger-eval-") as temp_dir:
        database = build_demo_database(Path(temp_dir) / "eval.duckdb")
        report = run_evaluation(
            suite,
            database_path=database,
            registry=registry,
            snapshots=snapshots,
            commit=_git("rev-parse", "HEAD"),
            worktree_dirty=bool(_git("status", "--porcelain")),
        )

    output_json = Path(args.output_json)
    output_markdown = Path(args.output_markdown)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(report.to_json() + "\n", encoding="utf-8")
    output_markdown.write_text(report.to_markdown(), encoding="utf-8")

    print(f"scenarios: {report.scenario_count}")
    for name, metrics in report.baselines.items():
        print(
            f"{name}: "
            f"success={metrics.scenario_success_count}/{metrics.scenario_count}, "
            f"unsafe={metrics.unsafe_answer_count}, "
            f"scope_violations={metrics.scope_violation_count}, "
            f"p95={metrics.elapsed_p95_ms:.2f}ms"
        )
    print(f"json: {output_json}")
    print(f"markdown: {output_markdown}")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


if __name__ == "__main__":
    main()
