from __future__ import annotations

import asyncio
import io
import sys
from types import SimpleNamespace

import pytest
from rich.console import Console

from openminion.cli.interactive.runtime import OpenMinionRuntime
from openminion.cli.interactive.terminal.shell import _handle_slash
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine
from openminion.cli.interactive.terminal.transcript import TerminalTranscript
from openminion.modules.brain.paths import resolve_brain_runtime_db_path
from openminion.modules.brain.runtime.goal.ledger import SQLiteGoalRunStepLedger
from openminion.modules.brain.runtime.goal.loop import SQLiteGoalRunStore
from openminion.modules.brain.schemas import Deliverable, Goal, SuccessCriterion
from openminion.modules.brain.storage.goals import SQLiteGoalStore
from openminion.modules.task import AutonomyRunStore, TaskManager
from openminion.modules.task.autonomy import resolve_autonomy_state_root
from openminion.modules.task.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH
from openminion.modules.task.project import load_latest_project_checkpoint

pytestmark = pytest.mark.e2e


class _StubOverlay:
    pass


class _ProjectSessions:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def append_event(self, **event) -> str:  # noqa: ANN003
        self.events.append(event)
        return f"event-{len(self.events)}"


class _ProjectTelemetry:
    def __init__(self) -> None:
        self.operations: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def emit_module_operation(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.operations.append((args, kwargs))


class _CronStore:
    def __init__(self) -> None:
        self.jobs: list[dict[str, object]] = []
        self.deleted: list[str] = []

    def add_cron_job(self, **job) -> None:  # noqa: ANN003
        self.jobs.append(job)

    def delete_cron_job(self, job_id: str) -> None:
        self.deleted.append(job_id)
        self.jobs = [job for job in self.jobs if job.get("job_id") != job_id]

    def close(self) -> None:
        pass


class _FailingCronStore(_CronStore):
    def add_cron_job(self, **job) -> None:  # noqa: ANN003
        raise RuntimeError("cron unavailable")


def _runtime(*, storage_path, session_id: str) -> OpenMinionRuntime:
    runtime = OpenMinionRuntime.__new__(OpenMinionRuntime)
    runtime._rt = SimpleNamespace(storage_path=storage_path)
    runtime._session_id = session_id
    return runtime


def _seed_goal(*, storage_path, session_id: str) -> tuple[str, str]:
    db_path = resolve_brain_runtime_db_path(storage_path=storage_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    goal_id = "goal-focus-e2e"
    store = SQLiteGoalStore(db_path)
    store.create(
        Goal(
            goal_id=goal_id,
            description="prove goal composition in focus",
            success_criteria=[
                SuccessCriterion(
                    criterion_id="criterion-focus",
                    description="focused proof passes",
                    structural_check="tests.pass=true",
                )
            ],
            deliverables=[
                Deliverable(
                    deliverable_id="deliverable-focus",
                    description="goal focus proof",
                )
            ],
        )
    )
    store.bind_to_session(goal_id, session_id)
    return goal_id, str(db_path)


async def _dispatch(
    text: str,
    *,
    runtime: OpenMinionRuntime,
    status_line: TerminalStatusLine,
    approval_callback=None,
) -> str:
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, width=160)
    await _handle_slash(
        text,
        runtime=runtime,
        console=console,
        transcript=TerminalTranscript(console),
        overlay=_StubOverlay(),  # type: ignore[arg-type]
        status_line=status_line,
        working_dir=runtime.working_dir,
        approval_callback=approval_callback,
    )
    return output.getvalue()


def _project_runtime(
    tmp_path,
) -> tuple[OpenMinionRuntime, _ProjectSessions, _ProjectTelemetry]:
    sessions = _ProjectSessions()
    telemetry = _ProjectTelemetry()
    runtime = OpenMinionRuntime.__new__(OpenMinionRuntime)
    runtime._rt = SimpleNamespace(
        config_path=tmp_path / "openminion.yaml",
        data_root=tmp_path / "data",
        home_root=tmp_path / "home",
        sessions=sessions,
        telemetry_service=telemetry,
    )
    runtime._session_id = "focus-project-session"
    runtime._agent_id = "alpha"
    runtime._gateway = object()
    runtime._working_dir = str(tmp_path)
    return runtime, sessions, telemetry


def test_terminal_project_launch_approval_persists_exact_repository(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    runtime, sessions, telemetry = _project_runtime(tmp_path)
    cron_store = _CronStore()
    monkeypatch.setattr(
        "openminion.cli.commands.autonomy_project.configured_cron_store",
        lambda *_args, **_kwargs: cron_store,
    )
    approval_args: dict[str, object] = {}

    async def approve(_name, args, _call_id) -> bool:  # noqa: ANN001
        approval_args.update(args)
        return True

    output = asyncio.run(
        _dispatch(
            f'/project start --repository "{repository}" --goal "ship it" '
            "--max-wall-clock-ms 60000 --max-tool-calls 12 "
            '--expected-check lint --expected-check "tests (3.11)" '
            f"--verify-command \"{sys.executable} -c 'print(1)'\" --release-tools",
            runtime=runtime,
            status_line=TerminalStatusLine(),
            approval_callback=approve,
        )
    )
    assert sessions.events, output
    event = sessions.events[0]
    run_id = str(event["payload"]["autonomy_run_id"])
    run = AutonomyRunStore(
        root=resolve_autonomy_state_root(runtime._rt.home_root)
    ).require(run_id)
    manager = TaskManager.for_lifecycle_db(
        db_path=(runtime._rt.data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH).resolve()
    )
    checkpoint = load_latest_project_checkpoint(manager, task_id=run.task_id or "")

    assert "Project queued" in output
    assert f"Project: prun_{run_id}" in output
    assert f"Task: {run.task_id}" in output
    assert event["event_type"] == "project.launched"
    assert event["task_id"] == run.task_id
    assert checkpoint is not None
    resume = checkpoint.payload["repository_lifecycle"][
        checkpoint.project_run.resume_packet_ref
    ]
    objective = checkpoint.payload["repository_lifecycle"][
        checkpoint.project_run.objective_ledger_ref
    ]
    decisions = checkpoint.payload["repository_lifecycle"][
        checkpoint.project_run.operator_decision_log_ref
    ]["decisions"]
    assert str(repository) in checkpoint.project_run.workspace_ref
    assert resume["task_plan_required"] is True
    assert resume["expected_checks"] == ["lint", "tests (3.11)"]
    assert resume["execution_repository"] == checkpoint.project_run.workspace_ref
    assert str(tmp_path) in resume["workspace_boundary"]
    assert objective["objective"] == "ship it"
    assert objective["approval"] == "approved"
    assert decisions == [
        {"decision": "project_launch_approved", "objective": "ship it"},
        {"decision": "project_release_tools_approved", "objective": "ship it"},
    ]
    assert telemetry.operations[0][0][3] == "project_launch"
    assert approval_args["permission_profile_id"] == "local-safe"
    assert approval_args["max_iterations"] == 1
    assert approval_args["max_wall_clock_ms"] == 60000
    assert approval_args["max_tool_calls"] == 12
    assert approval_args["verification_commands"] == [f"{sys.executable} -c 'print(1)'"]
    assert approval_args["expected_checks"] == ["lint", "tests (3.11)"]
    assert approval_args["release_tools"] is True
    assert approval_args["verification_waiver_reason"] is None
    assert approval_args["turn_timeout_seconds"] > 0
    assert approval_args["verification_timeout_seconds"] > 0
    task = manager.get_task(run.task_id or "")
    assert task is not None
    assert task.metadata["linked_cron_job_id"] == f"prun_{run_id}:wake:0"
    assert run.next_action_hint == f"Waiting for project cycle prun_{run_id}:wake:0."
    assert cron_store.jobs[0]["job_id"] == f"prun_{run_id}:wake:0"


def test_terminal_project_missing_verifier_blocks_without_wake(tmp_path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    runtime, sessions, _telemetry = _project_runtime(tmp_path)

    async def approve(*_args) -> bool:
        return True

    output = asyncio.run(
        _dispatch(
            f'/project start --repository "{repository}" --goal "ship it"',
            runtime=runtime,
            status_line=TerminalStatusLine(),
            approval_callback=approve,
        )
    )
    run = AutonomyRunStore(
        root=resolve_autonomy_state_root(runtime._rt.home_root)
    ).list_runs()[0]

    assert "Project blocked" in output
    assert f"Run: {run.run_id}" in output
    assert "rerun `/project start`" in output
    assert run.status.value == "blocked"
    assert sessions.events[0]["event_type"] == "project.launch_blocked"
    manager = TaskManager.for_lifecycle_db(
        db_path=(runtime._rt.data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH).resolve()
    )
    assert manager.get_task(run.task_id or "") is None


@pytest.mark.parametrize(
    "command",
    ["/project", "/project help", "/project start --help"],
)
def test_terminal_project_help_requires_no_approval(tmp_path, command: str) -> None:
    runtime, _sessions, _telemetry = _project_runtime(tmp_path)

    output = asyncio.run(
        _dispatch(
            command,
            runtime=runtime,
            status_line=TerminalStatusLine(),
            approval_callback=None,
        )
    )

    assert "/project start --goal TEXT" in output
    assert "--verify-command COMMAND" in output
    assert "--verification-domain coding|research" in output
    assert "--max-wall-clock-ms" in output
    assert "approval is unavailable" not in output


def test_terminal_project_wake_failure_is_recoverable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    runtime, sessions, _telemetry = _project_runtime(tmp_path)
    monkeypatch.setattr(
        "openminion.cli.commands.autonomy_project.configured_cron_store",
        lambda *_args, **_kwargs: _FailingCronStore(),
    )

    async def approve(*_args) -> bool:
        return True

    output = asyncio.run(
        _dispatch(
            f'/project start --repository "{repository}" --goal "ship it" '
            f"--verify-command \"{sys.executable} -c 'print(1)'\"",
            runtime=runtime,
            status_line=TerminalStatusLine(),
            approval_callback=approve,
        )
    )
    run = AutonomyRunStore(
        root=resolve_autonomy_state_root(runtime._rt.home_root)
    ).list_runs()[0]
    manager = TaskManager.for_lifecycle_db(
        db_path=(runtime._rt.data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH).resolve()
    )
    task = manager.get_task(run.task_id or "")

    assert "Project blocked: cron unavailable" in output
    assert "openminion autonomy resume" in output
    assert run.status.value == "blocked"
    assert task is not None and task.state.value == "paused"
    assert sessions.events[0]["payload"]["reason_code"] == "wake_schedule_failed"


def test_terminal_project_schedule_compensates_after_metadata_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    runtime, _sessions, _telemetry = _project_runtime(tmp_path)
    cron_store = _CronStore()
    monkeypatch.setattr(
        "openminion.cli.commands.autonomy_project.configured_cron_store",
        lambda *_args, **_kwargs: cron_store,
    )
    original_update = TaskManager.update_task_metadata
    calls = 0

    def fail_once(self, *, task_id, metadata):  # noqa: ANN001
        nonlocal calls
        if "linked_cron_job_id" in metadata:
            calls += 1
        if calls == 1 and "linked_cron_job_id" in metadata:
            raise RuntimeError("task metadata unavailable")
        return original_update(self, task_id=task_id, metadata=metadata)

    monkeypatch.setattr(TaskManager, "update_task_metadata", fail_once)

    async def approve(*_args) -> bool:
        return True

    output = asyncio.run(
        _dispatch(
            f'/project start --repository "{repository}" --goal "ship it" '
            f"--verify-command \"{sys.executable} -c 'print(1)'\"",
            runtime=runtime,
            status_line=TerminalStatusLine(),
            approval_callback=approve,
        )
    )

    run = AutonomyRunStore(
        root=resolve_autonomy_state_root(runtime._rt.home_root)
    ).list_runs()[0]
    manager = TaskManager.for_lifecycle_db(
        db_path=(runtime._rt.data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH).resolve()
    )
    task = manager.get_task(run.task_id or "")
    assert "Project blocked: task metadata unavailable" in output
    assert cron_store.jobs == []
    assert cron_store.deleted == [f"prun_{run.run_id}:wake:0"]
    assert task is not None
    assert "linked_cron_job_id" not in task.metadata


def test_terminal_project_denial_records_fact_without_creating_project(
    tmp_path,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    runtime, sessions, telemetry = _project_runtime(tmp_path)

    async def deny(*_args) -> bool:
        return False

    output = asyncio.run(
        _dispatch(
            f'/project start --repository "{repository}" --goal "do not ship"',
            runtime=runtime,
            status_line=TerminalStatusLine(),
            approval_callback=deny,
        )
    )

    assert "Project launch denied" in output
    assert sessions.events[0]["event_type"] == "project.launch_denied"
    assert sessions.events[0]["payload"]["reason_code"] == "operator_denied"
    assert (
        AutonomyRunStore(
            root=resolve_autonomy_state_root(runtime._rt.home_root)
        ).list_runs()
        == []
    )
    assert telemetry.operations[0][0][3] == "project_launch_denied"


def test_default_terminal_goal_run_persists_two_steps_and_renders_card(
    tmp_path,
) -> None:
    session_id = "focus-goal-session"
    storage_path = tmp_path / "openminion.db"
    runtime = _runtime(storage_path=storage_path, session_id=session_id)
    runtime._working_dir = str(tmp_path)
    goal_id, db_path_raw = _seed_goal(storage_path=storage_path, session_id=session_id)
    status_line = TerminalStatusLine()

    completed = asyncio.run(
        _dispatch(
            f"/goal run {goal_id} --live "
            "continue:need-focused-proof,satisfied:focused-proof-passes",
            runtime=runtime,
            status_line=status_line,
        )
    )
    assert "status=completed" in completed
    assert "turns=2/3" in completed

    db_path = resolve_brain_runtime_db_path(storage_path=storage_path)
    state = SQLiteGoalRunStore(db_path).latest_for_session(session_id)
    assert state is not None
    assert state.run_id
    steps = SQLiteGoalRunStepLedger(db_path).list_for_run(state.run_id)
    assert [step.evaluator_outcome for step in steps] == ["continue", "satisfied"]
    assert state.goal_id == goal_id
    assert state.status.value == "completed"
    assert str(db_path) == db_path_raw

    inspected = asyncio.run(
        _dispatch(
            "/goal inspect",
            runtime=runtime,
            status_line=status_line,
        )
    )
    assert "ledger_steps=2" in inspected
    assert f"Active goal: {goal_id}" in inspected
    assert "focused proof passes" in inspected
    assert "goal focus proof" in inspected
    assert "Caps: turns=2/3" in inspected


def test_default_terminal_goal_start_updates_status_and_cap_stop(tmp_path) -> None:
    session_id = "focus-goal-cap-session"
    storage_path = tmp_path / "openminion.db"
    runtime = _runtime(storage_path=storage_path, session_id=session_id)
    runtime._working_dir = str(tmp_path)
    goal_id, _db_path = _seed_goal(storage_path=storage_path, session_id=session_id)
    status_line = TerminalStatusLine()

    started = asyncio.run(
        _dispatch(
            f"/goal run {goal_id}",
            runtime=runtime,
            status_line=status_line,
        )
    )
    assert "status=active" in started
    assert status_line.custom_label == "goal: active turn 0 · started"

    paused = asyncio.run(
        _dispatch(
            "/goal pause",
            runtime=runtime,
            status_line=status_line,
        )
    )
    assert "status=paused" in paused

    resumed = asyncio.run(
        _dispatch(
            "/goal resume",
            runtime=runtime,
            status_line=status_line,
        )
    )
    assert "status=active" in resumed
    assert status_line.custom_label == "goal: active turn 0 · operator_resume"

    capped = asyncio.run(
        _dispatch(
            f"/goal run {goal_id} --live continue:still-working,continue:still-working,continue:still-working",
            runtime=runtime,
            status_line=status_line,
        )
    )
    assert "status=paused" in capped
    assert "turns=2/3" in capped
    assert '"repeated_no_progress_count":2' in capped


def test_default_terminal_goal_create_then_live_run_persists_steps(tmp_path) -> None:
    session_id = "focus-goal-create-session"
    storage_path = tmp_path / "openminion.db"
    runtime = _runtime(storage_path=storage_path, session_id=session_id)
    runtime._working_dir = str(tmp_path)
    status_line = TerminalStatusLine()

    created = asyncio.run(
        _dispatch(
            (
                '/goal create "finish focus-created goal" --id goal-focus-created '
                '--criterion "focused proof passes" '
                '--deliverable "focus-created proof"'
            ),
            runtime=runtime,
            status_line=status_line,
        )
    )
    assert "created goal-focus-created [active] finish focus-created goal" in created
    assert "bound=current-session" in created

    completed = asyncio.run(
        _dispatch(
            "/goal run goal-focus-created --live "
            "continue:need-focus-created-proof,satisfied:focus-created-proof-passes",
            runtime=runtime,
            status_line=status_line,
        )
    )
    assert "status=completed" in completed
    assert "turns=2/3" in completed

    db_path = resolve_brain_runtime_db_path(storage_path=storage_path)
    state = SQLiteGoalRunStore(db_path).latest_for_session(session_id)
    assert state is not None
    steps = SQLiteGoalRunStepLedger(db_path).list_for_run(state.run_id)
    assert [step.evaluator_outcome for step in steps] == ["continue", "satisfied"]

    inspected = asyncio.run(
        _dispatch(
            "/goal inspect",
            runtime=runtime,
            status_line=status_line,
        )
    )
    assert "ledger_steps=2" in inspected
    assert "focused proof passes" in inspected
    assert "focus-created proof" in inspected
