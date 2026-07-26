from __future__ import annotations

from openminion.cli.commands.goal import execute_goal_cli_command
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
    del monkeypatch
    _tone, output = execute_goal_cli_command(
        line,
        session_id=session_id,
        db_path=db_path,
    )
    return True, output


def test_goal_command_list_show_abort_and_verify(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "brain.db"
    store = SQLiteGoalStore(db_path)
    store.create(_goal("goal-1"))
    store.bind_to_session("goal-1", "sess-goal")

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


def test_goal_command_create_defaults_and_binds_to_current_session(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "brain.db"

    _, created = _capture(
        '/goal create "ship durable goal creation" --id goal-created',
        session_id="sess-goal",
        db_path=db_path,
        monkeypatch=monkeypatch,
    )

    assert "created goal-created [active] ship durable goal creation" in created
    assert "bound=current-session" in created

    store = SQLiteGoalStore(db_path)
    goal = store.get("goal-created")
    assert goal is not None
    assert goal.description == "ship durable goal creation"
    assert goal.success_criteria[0].description == "ship durable goal creation"
    assert goal.success_criteria[0].structural_check == "operator_review"
    assert goal.deliverables[0].description == "ship durable goal creation"
    assert store.is_bound_to_session("goal-created", "sess-goal") is True

    _, listed = _capture(
        "/goal list",
        session_id="sess-goal",
        db_path=db_path,
        monkeypatch=monkeypatch,
    )
    assert "goal-created [active] ship durable goal creation" in listed


def test_goal_command_create_no_bind_then_bind_existing_goal(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "brain.db"

    _, created = _capture(
        (
            '/goal create "prepare long task" --id goal-unbound '
            '--criterion "tests pass" --deliverable "operator report" --no-bind'
        ),
        session_id="sess-a",
        db_path=db_path,
        monkeypatch=monkeypatch,
    )
    assert "created goal-unbound [active] prepare long task" in created
    assert "bound=current-session" not in created

    _, empty = _capture(
        "/goal list",
        session_id="sess-a",
        db_path=db_path,
        monkeypatch=monkeypatch,
    )
    assert "No active goals for this session." in empty

    _, bound = _capture(
        "/goal bind goal-unbound",
        session_id="sess-a",
        db_path=db_path,
        monkeypatch=monkeypatch,
    )
    assert "bound goal-unbound [active] prepare long task" in bound

    store = SQLiteGoalStore(db_path)
    goal = store.get("goal-unbound")
    assert goal is not None
    assert goal.success_criteria[0].description == "tests pass"
    assert goal.deliverables[0].description == "operator report"
    assert store.is_bound_to_session("goal-unbound", "sess-a") is True


def test_goal_command_list_is_session_scoped_and_all_is_explicit(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "brain.db"
    store = SQLiteGoalStore(db_path)
    store.create(_goal("goal-a"))
    store.create(_goal("goal-b"))
    store.bind_to_session("goal-a", "sess-a")
    store.bind_to_session("goal-b", "sess-b")

    _, sess_a = _capture(
        "/goal list",
        session_id="sess-a",
        db_path=db_path,
        monkeypatch=monkeypatch,
    )
    assert "goal-a [active]" in sess_a
    assert "goal-b [active]" not in sess_a

    _, all_goals = _capture(
        "/goal all",
        session_id="sess-a",
        db_path=db_path,
        monkeypatch=monkeypatch,
    )
    assert "goal-a [active]" in all_goals
    assert "goal-b [active]" in all_goals

    _, goals_alias = _capture(
        "/goals",
        session_id="sess-a",
        db_path=db_path,
        monkeypatch=monkeypatch,
    )
    assert "goal-a [active]" in goals_alias
    assert "goal-b [active]" in goals_alias


def test_goal_command_rejects_cross_session_show_abort_verify(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "brain.db"
    store = SQLiteGoalStore(db_path)
    store.create(_goal("goal-b"))
    store.bind_to_session("goal-b", "sess-b")

    for command in (
        "/goal show goal-b",
        "/goal verify goal-b",
        "/goal abort goal-b",
        "/goal run goal-b",
    ):
        _, output = _capture(
            command,
            session_id="sess-a",
            db_path=db_path,
            monkeypatch=monkeypatch,
        )
        assert "not active for this session" in output


def test_goal_command_run_status_stop_and_replay_e2e(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "brain.db"
    store = SQLiteGoalStore(db_path)
    store.create(_goal("goal-run"))
    store.bind_to_session("goal-run", "sess-goal")

    _, started = _capture(
        "/goal run goal-run",
        session_id="sess-goal",
        db_path=db_path,
        monkeypatch=monkeypatch,
    )
    assert "goal=goal-run" in started
    assert "status=active" in started
    assert "turns=0/3" in started

    _, status = _capture(
        "/goal status",
        session_id="sess-goal",
        db_path=db_path,
        monkeypatch=monkeypatch,
    )
    assert "goal=goal-run" in status

    _, stopped = _capture(
        "/goal stop",
        session_id="sess-goal",
        db_path=db_path,
        monkeypatch=monkeypatch,
    )
    assert "status=cancelled" in stopped

    _, replayed = _capture(
        "/goal run goal-run --replay continue:needs-more-tests,satisfied:done",
        session_id="sess-goal",
        db_path=db_path,
        monkeypatch=monkeypatch,
    )
    assert "status=completed" in replayed
    assert "turns=2/3" in replayed
    assert "latest_reason=done" in replayed
    assert "goal-run-proofs/proof/" in replayed


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
