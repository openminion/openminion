from __future__ import annotations

from pathlib import Path

from tests.brain.diagnostics.phase_contract_eval import (
    default_phase_contract_scenarios,
    results_to_json,
    run_fixture_trace_scenario,
)


def test_fixture_eval_scenarios_pass_expected_issue_profiles() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    scenarios = {
        item.scenario_id: item for item in default_phase_contract_scenarios(repo_root)
    }

    invalid_decide = run_fixture_trace_scenario(scenarios["invalid_decide_fixture"])
    invalid_judge = run_fixture_trace_scenario(scenarios["invalid_judge_fixture"])
    malformed = run_fixture_trace_scenario(scenarios["malformed_wrapper_fixture"])

    assert invalid_decide.passed is True
    assert invalid_judge.passed is True
    assert malformed.passed is True


def test_eval_results_json_reports_pass_fail_counts() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    scenarios = {
        item.scenario_id: item for item in default_phase_contract_scenarios(repo_root)
    }
    results = [
        run_fixture_trace_scenario(scenarios["invalid_decide_fixture"]),
        run_fixture_trace_scenario(scenarios["invalid_judge_fixture"]),
    ]

    payload = results_to_json(results)

    assert payload["passed"] == 2
    assert payload["failed"] == 0
    assert len(payload["results"]) == 2
