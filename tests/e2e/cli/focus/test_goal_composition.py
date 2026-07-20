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
from openminion.modules.brain.paths import resolve_brain_sessions_db_path
from openminion.modules.brain.runtime.goal.ledger import SQLiteGoalRunStepLedger
from openminion.modules.brain.runtime.goal.loop import SQLiteGoalRunStore
from openminion.modules.brain.schemas import Deliverable, Goal, SuccessCriterion
from openminion.modules.brain.storage.goals import SQLiteGoalStore


pytestmark = pytest.mark.e2e


class _StubOverlay:
    pass


def _runtime(*, storage_path, session_id: str) -> OpenMinionRuntime:
    runtime = OpenMinionRuntime.__new__(OpenMinionRuntime)
    runtime._rt = SimpleNamespace(storage_path=storage_path)
    runtime._session_id = session_id
    return runtime


def _seed_goal(*, storage_path, session_id: str) -> tuple[str, str]:
    db_path = resolve_brain_sessions_db_path(storage_path=storage_path)
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
    )
    return output.getvalue()


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

    db_path = resolve_brain_sessions_db_path(storage_path=storage_path)
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
