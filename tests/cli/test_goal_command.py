from __future__ import annotations

import io
from contextlib import redirect_stdout
from types import SimpleNamespace

from openminion.cli.chat.commands.goal import handle_goal_command
from openminion.modules.brain.schemas import Deliverable, Goal, SuccessCriterion
from openminion.modules.brain.storage.goals import SQLiteGoalStore


def _goal(goal_id: str) -> Goal:
    return Goal(
        goal_id=goal_id,
        description=f"goal {goal_id}",
        success_criteria=[
            SuccessCriterion(
                criterion_id="criterion-1",
                description="criterion",
                structural_check="success_criteria.ok=true",
            )
        ],
        deliverables=[
            Deliverable(
                deliverable_id="deliverable-1",
                description="deliverable",
            )
        ],
    )


def _capture(
    line: str,
    *,
    session_id: str,
    db_path,
    monkeypatch,
) -> tuple[bool, str]:
    monkeypatch.setattr(
        "openminion.cli.chat.commands.goal.resolve_cli_roots",
        lambda **_kwargs: SimpleNamespace(data_root=db_path.parent),
    )
    monkeypatch.setattr(
        "openminion.cli.chat.commands.goal.resolve_brain_runtime_db_path",
        lambda *, storage_path: db_path,
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_goal_command(line, session_id=session_id, config_path=None)
    return handled, buf.getvalue()


def test_goal_command_list_show_abort_and_verify(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "brain.db"
    store = SQLiteGoalStore(db_path)
    store.create(_goal("goal-1"))

    handled, listed = _capture(
        "/goal list",
        session_id="sess-goal",
        db_path=db_path,
        monkeypatch=monkeypatch,
    )
    assert handled is True
    assert "goal-1 [active] goal goal-1" in listed

    _, shown = _capture(
        "/goal show goal-1",
        session_id="sess-goal",
        db_path=db_path,
        monkeypatch=monkeypatch,
    )
    assert "success_criteria=1" in shown
    assert "deliverables=1" in shown

    _, verified = _capture(
        "/goal verify goal-1",
        session_id="sess-goal",
        db_path=db_path,
        monkeypatch=monkeypatch,
    )
    assert "goal=goal-1" in verified
    assert "status=" in verified

    _, aborted = _capture(
        "/goal abort goal-1",
        session_id="sess-goal",
        db_path=db_path,
        monkeypatch=monkeypatch,
    )
    assert "[cancelled]" in aborted


def test_goal_command_unknown_subaction_prints_usage(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "brain.db"

    handled, output = _capture(
        "/goal frobnicate",
        session_id="sess-goal",
        db_path=db_path,
        monkeypatch=monkeypatch,
    )

    assert handled is True
    assert "usage: /goal" in output
