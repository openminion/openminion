from __future__ import annotations

from tests.e2e.project_worker.harness import (
    ProjectWorkerScenarioKind,
    local_scenario_ids,
    scenario_ids,
    scenarios_for_suite,
    soak_scenario_ids,
)
from tests.e2e.runners.run_project_worker_e2e import suite_names


def test_project_worker_harness_covers_required_scenario_kinds() -> None:
    kinds = {scenario.kind for scenario in scenarios_for_suite("all")}

    assert {
        ProjectWorkerScenarioKind.AUTONOMY_CLI,
        ProjectWorkerScenarioKind.CHAT_CLI,
        ProjectWorkerScenarioKind.FOCUS_PTY,
        ProjectWorkerScenarioKind.RESTART_RESUME,
        ProjectWorkerScenarioKind.PERMISSION,
        ProjectWorkerScenarioKind.CODING,
        ProjectWorkerScenarioKind.RESEARCH,
        ProjectWorkerScenarioKind.HUMAN_INPUT,
        ProjectWorkerScenarioKind.FAILURE_RECOVERY,
        ProjectWorkerScenarioKind.REPORT,
        ProjectWorkerScenarioKind.PILOT,
        ProjectWorkerScenarioKind.SOAK,
    } <= kinds


def test_project_worker_harness_supports_single_and_deep_suites() -> None:
    ids = set(scenario_ids())
    local_ids = set(local_scenario_ids())
    soak_ids = set(soak_scenario_ids())

    assert "project-report-local" in ids
    assert "focus-live-soak" in soak_ids
    assert local_ids < ids
    assert set(suite_names()) == {"local", "pilot", "live", "deep", "soak", "all"}
    assert scenarios_for_suite("project-report-local")[0].scenario_id == (
        "project-report-local"
    )
    assert len(scenarios_for_suite("pilot")) == 2
    assert len(scenarios_for_suite("deep")) >= 3
