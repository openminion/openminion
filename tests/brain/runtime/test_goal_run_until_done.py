from __future__ import annotations

from openminion.modules.brain.constants import MissionStatus
from openminion.modules.brain.runtime.goal.loop import (
    GoalRunCaps,
    GoalRunController,
    GoalRunEvaluation,
    SQLiteGoalRunStore,
    build_continuation_prompt,
    format_goal_focus_segment,
    parse_replay_evaluations,
)
from openminion.modules.brain.schemas import Deliverable, Goal, SuccessCriterion
from openminion.modules.brain.storage.goals import SQLiteGoalStore


def _goal(goal_id: str = "goal-run") -> Goal:
    return Goal(
        goal_id=goal_id,
        description="ship the goal loop",
        success_criteria=[
            SuccessCriterion(
                criterion_id="criterion-1",
                description="criterion",
                structural_check="success_criteria.ok=true",
            )
        ],
        deliverables=[Deliverable(deliverable_id="deliverable-1", description="doc")],
    )


def _controller(tmp_path):
    db_path = tmp_path / "brain.db"
    goal_store = SQLiteGoalStore(db_path)
    goal_store.create(_goal())
    goal_store.bind_to_session("goal-run", "sess-goal")
    return GoalRunController(
        goal_store=goal_store,
        run_store=SQLiteGoalRunStore(db_path),
    ), goal_store


def _evaluation(outcome: str, reason: str = "reason") -> GoalRunEvaluation:
    return GoalRunEvaluation(
        goal_id="goal-run",
        outcome=outcome,  # type: ignore[arg-type]
        mission_status={
            "satisfied": MissionStatus.COMPLETED,
            "continue": MissionStatus.ACTIVE,
            "blocked": MissionStatus.PAUSED,
            "needs_user": MissionStatus.PAUSED,
            "awaiting_async": MissionStatus.AWAITING_ASYNC,
            "halted": MissionStatus.HALTED,
        }[outcome],
        reason=reason,
        evidence_refs=("evidence:1",),
        next_instruction="keep going" if outcome == "continue" else "",
    )


def test_goal_run_replay_continues_then_completes_with_awrk_adapter(tmp_path) -> None:
    controller, goal_store = _controller(tmp_path)

    final_state = controller.run_replay(
        session_id="sess-goal",
        goal_id="goal-run",
        evaluations=(
            _evaluation("continue", "tests still failing"),
            _evaluation("satisfied", "tests pass"),
        ),
    )

    assert final_state.active is False
    assert final_state.status == MissionStatus.COMPLETED
    assert final_state.turn_count == 2
    assert final_state.proof_packet_ref == "awrk-proof:" + final_state.run_id
    assert goal_store.get("goal-run").status == MissionStatus.COMPLETED  # type: ignore[union-attr]


def test_goal_run_covers_all_terminal_outcomes(tmp_path) -> None:
    for outcome, expected in (
        ("blocked", MissionStatus.PAUSED),
        ("needs_user", MissionStatus.PAUSED),
        ("awaiting_async", MissionStatus.AWAITING_ASYNC),
        ("halted", MissionStatus.HALTED),
    ):
        controller, _goal_store = _controller(tmp_path / outcome)
        final_state = controller.run_replay(
            session_id="sess-goal",
            goal_id="goal-run",
            evaluations=(_evaluation(outcome, f"{outcome} reason"),),
        )
        assert final_state.active is False
        assert final_state.status == expected
        assert final_state.last_evaluator_reason == f"{outcome} reason"


def test_goal_run_evaluation_rejects_status_mapping_drift() -> None:
    try:
        GoalRunEvaluation(
            goal_id="goal-run",
            outcome="satisfied",
            mission_status=MissionStatus.ACTIVE,
            reason="wrong status",
        )
    except ValueError as exc:
        assert "mission_status must match" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected mismatched status mapping to fail")


def test_goal_run_caps_stop_continuation_before_infinite_loop(tmp_path) -> None:
    controller, _goal_store = _controller(tmp_path)

    final_state = controller.run_replay(
        session_id="sess-goal",
        goal_id="goal-run",
        caps=GoalRunCaps(max_auto_turns=1),
        evaluations=(
            _evaluation("continue", "not done"),
            _evaluation("satisfied", "unreachable"),
        ),
    )

    assert final_state.active is False
    assert final_state.status == MissionStatus.PAUSED
    assert final_state.turn_count == 1
    assert final_state.last_evaluator_reason == "not done"


def test_repeated_no_progress_reason_pauses(tmp_path) -> None:
    controller, _goal_store = _controller(tmp_path)

    final_state = controller.run_replay(
        session_id="sess-goal",
        goal_id="goal-run",
        evaluations=(
            _evaluation("continue", "same blocker"),
            _evaluation("continue", "same blocker"),
            _evaluation("satisfied", "unreachable"),
        ),
    )

    assert final_state.active is False
    assert final_state.status == MissionStatus.PAUSED
    assert final_state.repeated_no_progress_count == 2


def test_continuation_prompt_is_structural_and_short() -> None:
    state = _controller_state_for_prompt()
    evaluation = _evaluation("continue", "need one more validation")

    prompt = build_continuation_prompt(state, evaluation)

    assert "Continue goal goal-run." in prompt
    assert "Evaluator outcome: continue." in prompt
    assert "need one more validation" in prompt
    assert "Evidence: evidence:1" in prompt


def test_replay_parser_rejects_prose_only_status() -> None:
    parsed = parse_replay_evaluations(
        "goal-run",
        "continue:need tests,satisfied:done",
    )
    assert [item.outcome for item in parsed] == ["continue", "satisfied"]

    try:
        parse_replay_evaluations("goal-run", "looks done maybe")
    except ValueError as exc:
        assert "outcome:reason" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected invalid replay syntax to fail")


def test_focus_segment_hides_inactive_state(tmp_path) -> None:
    controller, _goal_store = _controller(tmp_path)
    state = controller.start_goal_run(session_id="sess-goal", goal_id="goal-run")

    assert "goal: active turn 0" in format_goal_focus_segment(state)
    stopped = controller.stop_session_run(session_id="sess-goal")
    assert format_goal_focus_segment(stopped) == ""


def _controller_state_for_prompt():
    from openminion.modules.brain.runtime.goal.loop import GoalRunState

    return GoalRunState(
        run_id="run-1",
        session_id="sess-goal",
        goal_id="goal-run",
        started_at_ms=1,
        updated_at_ms=1,
    )
