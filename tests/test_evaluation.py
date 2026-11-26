from __future__ import annotations

from pathlib import Path

import pytest

from metricledger.evaluation import (
    METRICLEDGER,
    NAIVE,
    SEMANTIC_ONLY,
    load_suite,
    run_evaluation,
)
from metricledger.semantic import SemanticRegistry
from metricledger.snapshots import SnapshotRepository
from scripts.build_demo_data import DEMO_SNAPSHOTS, build_demo_database


@pytest.fixture(scope="module")
def suite():
    return load_suite()


@pytest.fixture(scope="module")
def report(suite, tmp_path_factory):
    database = build_demo_database(
        tmp_path_factory.mktemp("evaluation") / "eval.duckdb"
    )
    return run_evaluation(
        suite,
        database_path=database,
        registry=SemanticRegistry.load_default(),
        snapshots=SnapshotRepository.from_records(DEMO_SNAPSHOTS),
        commit="test-commit",
        worktree_dirty=True,
    )


def test_suite_contains_at_least_seventy_two_unique_scenarios(suite):
    assert len(suite.scenarios) >= 72
    assert len({scenario.scenario_id for scenario in suite.scenarios}) == len(
        suite.scenarios
    )
    assert len(
        {
            (
                scenario.question,
                scenario.as_of.isoformat(),
                scenario.principal.principal_id,
            )
            for scenario in suite.scenarios
        }
    ) == len(suite.scenarios)


def test_metricledger_has_no_unsafe_answers_on_forbidden_cases(report):
    metrics = report.baselines[METRICLEDGER]

    assert metrics.unsafe_answer_count == 0
    assert metrics.scope_violation_count == 0
    assert metrics.zero_as_empty_count == 0
    assert metrics.unsupported_claim_count == 0
    assert metrics.evidence_violation_count == 0
    assert metrics.budget_violation_count == 0


def test_metricledger_completes_bounded_investigations(report):
    metrics = report.baselines[METRICLEDGER]
    investigation_results = [
        result
        for result in report.results[METRICLEDGER]
        if result.category == "supported_investigation"
    ]

    assert metrics.supported_investigation_count > 0
    assert all(result.scenario_success for result in investigation_results)
    assert all(
        result.outcome.claim_count >= 2 for result in investigation_results
    )
    assert (
        metrics.completed_supported_investigation_count
        > report.baselines[SEMANTIC_ONLY].completed_supported_investigation_count
    )
    assert (
        metrics.scenario_success_count
        > report.baselines[SEMANTIC_ONLY].scenario_success_count
    )


def test_unsafe_baseline_exposes_the_guards(report):
    metrics = report.baselines[NAIVE]

    assert metrics.scope_violation_count > 0
    assert metrics.zero_as_empty_count > 0
    assert metrics.unsafe_answer_count > 0


def test_report_does_not_claim_external_benchmark(report):
    text = report.to_markdown()

    assert "SOTA" not in text
    assert "本仓库合成场景" in text
    assert "未使用 LLM judge" in text
    assert str(report.scenario_count) in text
    assert "max_planner_rounds" in text


def test_report_persists_exact_counts_and_configuration(report):
    payload = report.to_dict()

    assert payload["scenario_count"] == len(report.results[METRICLEDGER])
    assert payload["environment"]["snapshot_row_count"] == 5_184
    assert payload["environment"]["max_planner_rounds"] == 2
    assert isinstance(
        payload["environment"]["budget_exhausted_count"], int
    )
    assert payload["commit"] == "test-commit"
    assert payload["worktree_dirty"] is True
    assert set(payload["baselines"]) == {
        METRICLEDGER,
        SEMANTIC_ONLY,
        NAIVE,
    }


def test_report_uses_repository_relative_scenario_path(report):
    assert report.source_path == "eval/scenarios.json"


def test_scenario_file_is_a_real_repository_artifact():
    assert Path("eval/scenarios.json").is_file()
