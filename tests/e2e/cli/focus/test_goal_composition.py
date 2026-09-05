from __future__ import annotations

import asyncio
import io
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


def _project_runtime(tmp_path) -> tuple[OpenMinionRuntime, _ProjectSessions, _ProjectTelemetry]:
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


def test_terminal_project_launch_approval_persists_exact_repository(tmp_path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    runtime, sessions, telemetry = _project_runtime(tmp_path)
    approval_args: dict[str, object] = {}

    async def approve(_name, args, _call_id) -> bool:  # noqa: ANN001
        approval_args.update(args)
        return True

    output = asyncio.run(
        _dispatch(
            f'/project start --repository "{repository}" --goal "ship it"',
            runtime=runtime,
            status_line=TerminalStatusLine(),
            approval_callback=approve,
        )
    )
    event = sessions.events[0]
    run_id = str(event["payload"]["autonomy_run_id"])
    run = AutonomyRunStore(
        root=resolve_autonomy_state_root(runtime._rt.home_root)
    ).require(run_id)
    manager = TaskManager.for_lifecycle_db(
        db_path=(
            runtime._rt.data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH
        ).resolve()
    )
    checkpoint = load_latest_project_checkpoint(manager, task_id=run.task_id or "")

    assert "Project started" in output
    assert f"Project: prun_{run_id}" in output
    assert f"Task: {run.task_id}" in output
    assert event["event_type"] == "project.launched"
    assert event["task_id"] == run.task_id
    assert checkpoint is not None
    resume = checkpoint.payload["repository_lifecycle"][
        checkpoint.project_run.resume_packet_ref
    ]
    assert str(repository) in checkpoint.project_run.workspace_ref
    assert resume["task_plan_required"] is True
    assert resume["execution_repository"] == checkpoint.project_run.workspace_ref
    assert str(tmp_path) in resume["workspace_boundary"]
    assert telemetry.operations[0][0][3] == "project_launch"
    assert approval_args["permission_profile_id"] == "local-safe"
    assert approval_args["max_iterations"] == 1
    assert approval_args["verification_commands"] == []
    assert approval_args["verification_waiver_reason"] is None
    assert approval_args["turn_timeout_seconds"] > 0
    assert approval_args["verification_timeout_seconds"] > 0


def test_terminal_project_denial_records_fact_without_creating_project(tmp_path) -> None:
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
    assert AutonomyRunStore(
        root=resolve_autonomy_state_root(runtime._rt.home_root)
    ).list_runs() == []
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
