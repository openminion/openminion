from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ProjectWorkerScenarioKind(StrEnum):
    AUTONOMY_CLI = "autonomy_cli"
    CHAT_CLI = "chat_cli"
    FOCUS_PTY = "focus_pty"
    RESTART_RESUME = "restart_resume"
    PERMISSION = "permission"
    CODING = "coding"
    RESEARCH = "research"
    HUMAN_INPUT = "human_input"
    FAILURE_RECOVERY = "failure_recovery"
    REPORT = "report"
    PILOT = "pilot"
    SOAK = "soak"


@dataclass(frozen=True)
class ProjectWorkerScenario:
    scenario_id: str
    kind: ProjectWorkerScenarioKind
    suite: str
    pytest_targets: tuple[str, ...]
    live: bool = False
    complex: bool = False
    description: str = ""


PROJECT_WORKER_SCENARIOS: tuple[ProjectWorkerScenario, ...] = (
    ProjectWorkerScenario(
        scenario_id="project-report-local",
        kind=ProjectWorkerScenarioKind.REPORT,
        suite="local",
        pytest_targets=("tests/task/test_project_run.py",),
        description="Typed project report, capability matrix, and metric fixture.",
    ),
    ProjectWorkerScenario(
        scenario_id="autonomy-cli-project-controls",
        kind=ProjectWorkerScenarioKind.AUTONOMY_CLI,
        suite="local",
        pytest_targets=("tests/cli/test_autonomy_command.py",),
        description="Autonomy project controls and report command.",
    ),
    ProjectWorkerScenario(
        scenario_id="restart-resume-local",
        kind=ProjectWorkerScenarioKind.RESTART_RESUME,
        suite="local",
        pytest_targets=("tests/task/test_project_run.py",),
        description="Checkpoint, restart, resume, and duplicate-worker guard.",
    ),
    ProjectWorkerScenario(
        scenario_id="permission-local",
        kind=ProjectWorkerScenarioKind.PERMISSION,
        suite="local",
        pytest_targets=(
            "tests/task/test_project_run.py",
            "tests/tools/test_policy_commands.py",
            "tests/tools/exec/test_plugin.py",
        ),
        description="Project permission grants plus exec/tool policy contracts.",
    ),
    ProjectWorkerScenario(
        scenario_id="human-input-local",
        kind=ProjectWorkerScenarioKind.HUMAN_INPUT,
        suite="local",
        pytest_targets=("tests/cli/test_autonomy_command.py",),
        description="Operator answer capture for blocked project runs.",
    ),
    ProjectWorkerScenario(
        scenario_id="focus-local",
        kind=ProjectWorkerScenarioKind.FOCUS_PTY,
        suite="local",
        pytest_targets=("tests/e2e/cli/focus/test_local.py",),
        description="Local PTY launch and slash-command smoke.",
    ),
    ProjectWorkerScenario(
        scenario_id="chat-cli-local",
        kind=ProjectWorkerScenarioKind.CHAT_CLI,
        suite="local",
        pytest_targets=(
            "tests/e2e/test_run_cli_chat_probe.py",
            "tests/e2e/test_cli_chat_probe_runner.py",
        ),
        description="Local chat CLI probe runner coverage.",
    ),
    ProjectWorkerScenario(
        scenario_id="failure-report-local",
        kind=ProjectWorkerScenarioKind.FAILURE_RECOVERY,
        suite="local",
        pytest_targets=(
            "tests/e2e/cli/focus/test_harness_assertions.py",
            "tests/e2e/cli/focus/test_local.py",
        ),
        description="Transcript assertions and unresolved-approval failure detection.",
    ),
    ProjectWorkerScenario(
        scenario_id="pilot-30m-local",
        kind=ProjectWorkerScenarioKind.PILOT,
        suite="pilot",
        pytest_targets=("tests/e2e/project_worker/test_pilots.py",),
        description="Compressed 30-minute local fixture pilot report.",
    ),
    ProjectWorkerScenario(
        scenario_id="pilot-2h-coding-research",
        kind=ProjectWorkerScenarioKind.PILOT,
        suite="pilot",
        pytest_targets=("tests/e2e/project_worker/test_pilots.py",),
        description="Compressed 2-hour coding/research pilot report.",
    ),
    ProjectWorkerScenario(
        scenario_id="pilot-24h-restart-resume",
        kind=ProjectWorkerScenarioKind.SOAK,
        suite="soak",
        pytest_targets=("tests/e2e/project_worker/test_pilots.py",),
        description="Compressed 24-hour restart/resume pilot report.",
    ),
    ProjectWorkerScenario(
        scenario_id="pilot-72h-multiday",
        kind=ProjectWorkerScenarioKind.SOAK,
        suite="soak",
        pytest_targets=("tests/e2e/project_worker/test_pilots.py",),
        description="Compressed 72-hour multi-day pilot report.",
    ),
    ProjectWorkerScenario(
        scenario_id="focus-live-tools",
        kind=ProjectWorkerScenarioKind.FOCUS_PTY,
        suite="live",
        pytest_targets=("tests/e2e/cli/focus/test_live_tools.py",),
        live=True,
        description="MiniMax Focus tool and policy recovery scenarios.",
    ),
    ProjectWorkerScenario(
        scenario_id="focus-live-research",
        kind=ProjectWorkerScenarioKind.RESEARCH,
        suite="deep",
        pytest_targets=("tests/e2e/cli/focus/test_live_complex.py",),
        live=True,
        complex=True,
        description="Long research and synthesis scenarios.",
    ),
    ProjectWorkerScenario(
        scenario_id="focus-live-coding",
        kind=ProjectWorkerScenarioKind.CODING,
        suite="deep",
        pytest_targets=("tests/e2e/cli/focus/test_live_complex.py",),
        live=True,
        complex=True,
        description="Long coding and debug-loop scenarios.",
    ),
    ProjectWorkerScenario(
        scenario_id="focus-live-soak",
        kind=ProjectWorkerScenarioKind.SOAK,
        suite="soak",
        pytest_targets=("tests/e2e/cli/focus/test_live_soak.py",),
        live=True,
        complex=True,
        description="Long-running mixed coding/research Focus soak.",
    ),
)


def scenario_ids() -> tuple[str, ...]:
    return tuple(scenario.scenario_id for scenario in PROJECT_WORKER_SCENARIOS)


def local_scenario_ids() -> tuple[str, ...]:
    return tuple(
        scenario.scenario_id
        for scenario in PROJECT_WORKER_SCENARIOS
        if not scenario.live
    )


def soak_scenario_ids() -> tuple[str, ...]:
    return tuple(
        scenario.scenario_id
        for scenario in PROJECT_WORKER_SCENARIOS
        if scenario.suite in {"deep", "soak"}
        or scenario.kind == ProjectWorkerScenarioKind.SOAK
    )


def scenarios_for_suite(suite: str) -> tuple[ProjectWorkerScenario, ...]:
    requested = suite.strip().lower()
    by_id = {scenario.scenario_id: scenario for scenario in PROJECT_WORKER_SCENARIOS}
    if requested in by_id:
        return (by_id[requested],)
    if requested == "all":
        return PROJECT_WORKER_SCENARIOS
    if requested == "local":
        return tuple(
            scenario for scenario in PROJECT_WORKER_SCENARIOS if not scenario.live
        )
    if requested == "deep":
        return tuple(
            scenario
            for scenario in PROJECT_WORKER_SCENARIOS
            if scenario.suite in {"deep", "soak"}
        )
    return tuple(
        scenario for scenario in PROJECT_WORKER_SCENARIOS if scenario.suite == requested
    )


__all__ = (
    "PROJECT_WORKER_SCENARIOS",
    "ProjectWorkerScenario",
    "ProjectWorkerScenarioKind",
    "local_scenario_ids",
    "scenario_ids",
    "scenarios_for_suite",
    "soak_scenario_ids",
)
