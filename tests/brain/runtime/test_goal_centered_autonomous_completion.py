from __future__ import annotations

from pathlib import Path
from typing import cast

from openminion.modules.brain.constants import MissionStatus
from openminion.modules.brain.runtime.goal.context import (
    build_goal_context_card,
    render_goal_context_card,
)
from openminion.modules.brain.runtime.goal.driver import (
    GoalContinuationDriver,
    build_child_task_step,
    build_learning_candidate,
    resume_goal_after_async_wake,
)
from openminion.modules.brain.runtime.goal.evaluator import (
    GoalLiveEvaluationInput,
    GoalLiveEvaluator,
    GoalTurnResult,
)
from openminion.modules.brain.runtime.goal.ledger import (
    GoalRunStep,
    SQLiteGoalRunStepLedger,
)
from openminion.modules.brain.runtime.goal.loop import (
    GoalRunCaps,
    GoalRunController,
    GoalRunOutcome,
    SQLiteGoalRunStore,
)
from openminion.modules.brain.runtime.goal.verification import GoalVerificationResult
from openminion.modules.brain.schemas.goals import Deliverable, Goal, SuccessCriterion
from openminion.modules.brain.storage.goals import GoalStore, SQLiteGoalStore
from openminion.cli.commands.goal import execute_goal_cli_command


def _goal(goal_id: str = "goal-run") -> Goal:
    return Goal(
        goal_id=goal_id,
        description="ship autonomous completion",
        success_criteria=[
            SuccessCriterion(
                criterion_id="criterion-1",
                description="focused tests pass",
                structural_check="tests.pass=true",
            )
        ],
        deliverables=[Deliverable(deliverable_id="deliverable-1", description="proof")],
    )


def _passed_verification() -> GoalVerificationResult:
    return GoalVerificationResult(
        status="passed",
        unmet_criteria=(),
        missing_deliverables=(),
        triggered_failures=(),
        verifier_results=(),
    )


def _controller(tmp_path: Path) -> tuple[GoalRunController, GoalStore, Path]:
    db_path = tmp_path / "brain.db"
    goal_store: GoalStore = SQLiteGoalStore(db_path)
    goal_store.create(_goal())
    goal_store.bind_to_session("goal-run", "sess-goal")
    controller = GoalRunController(
        goal_store=goal_store,
        run_store=SQLiteGoalRunStore(db_path),
    )
    return controller, goal_store, db_path


def test_live_evaluator_covers_all_outcomes_and_requires_verification_for_satisfied() -> (
    None
):
    evaluator = GoalLiveEvaluator()
    expected = {
        "continue": MissionStatus.ACTIVE,
        "blocked": MissionStatus.PAUSED,
        "needs_user": MissionStatus.PAUSED,
        "awaiting_async": MissionStatus.AWAITING_ASYNC,
        "halted": MissionStatus.HALTED,
    }
    for outcome, status in expected.items():
        evaluation = evaluator.evaluate(
            GoalLiveEvaluationInput(
                goal_id="goal-run",
                turn_result=GoalTurnResult(
                    proposed_outcome=cast(GoalRunOutcome, outcome),
                    reason=f"{outcome} reason",
                ),
            )
        )
        assert evaluation.outcome == outcome
        assert evaluation.mission_status == status

    blocked_satisfaction = evaluator.evaluate(
        GoalLiveEvaluationInput(
            goal_id="goal-run",
            turn_result=GoalTurnResult(
                proposed_outcome="satisfied",
                reason="assistant says done",
            ),
        )
    )
    assert blocked_satisfaction.outcome == "continue"
    assert blocked_satisfaction.reason == "verification_required:not_checked"

    verified_satisfaction = evaluator.evaluate(
        GoalLiveEvaluationInput(
            goal_id="goal-run",
            turn_result=GoalTurnResult(proposed_outcome="satisfied", reason="done"),
            verification=_passed_verification(),
        )
    )
    assert verified_satisfaction.outcome == "satisfied"
    assert verified_satisfaction.mission_status == MissionStatus.COMPLETED


def test_ledger_preserves_wrong_turns_and_compact_summary(tmp_path: Path) -> None:
    ledger = SQLiteGoalRunStepLedger(tmp_path / "brain.db")
    ledger.append(
        GoalRunStep(
            run_id="run-1",
            session_id="sess-goal",
            goal_id="goal-run",
            turn_index=1,
            started_at_ms=1,
            ended_at_ms=2,
            action_summary="wrong branch",
            tool_evidence_refs=("tool:bad",),
            evaluator_outcome="continue",
            mission_status=MissionStatus.ACTIVE,
            evaluator_reason="wrong turn preserved",
            next_instruction="try the other branch",
            error_refs=("err:1",),
        )
    )
    ledger.append(
        GoalRunStep(
            run_id="run-1",
            session_id="sess-goal",
            goal_id="goal-run",
            turn_index=2,
            started_at_ms=3,
            ended_at_ms=4,
            action_summary="better branch",
            tool_evidence_refs=("tool:good",),
            evaluator_outcome="blocked",
            mission_status=MissionStatus.PAUSED,
            evaluator_reason="needs user",
        )
    )

    summary = ledger.summary_for_run("run-1")

    assert summary.step_count == 2
    assert summary.latest_outcome == "blocked"
    assert summary.evidence_refs == ("tool:bad", "tool:good")
    assert summary.error_refs == ("err:1",)


def test_context_card_is_bounded_goal_state(tmp_path: Path) -> None:
    controller, goal_store, db_path = _controller(tmp_path)
    state = controller.start_goal_run(session_id="sess-goal", goal_id="goal-run")
    ledger = SQLiteGoalRunStepLedger(db_path)
    ledger.append(
        GoalRunStep(
            run_id=state.run_id,
            session_id="sess-goal",
            goal_id="goal-run",
            turn_index=1,
            started_at_ms=1,
            ended_at_ms=2,
            tool_evidence_refs=("pytest:focused",),
            evaluator_outcome="continue",
            mission_status=MissionStatus.ACTIVE,
            evaluator_reason="need docs",
            next_instruction="update docs",
        )
    )

    goal = goal_store.get("goal-run")
    assert goal is not None
    card = build_goal_context_card(
        goal=goal,
        state=state,
        summary=ledger.summary_for_run(state.run_id),
    )
    rendered = render_goal_context_card(card)

    assert "Active goal: goal-run" in rendered
    assert "focused tests pass" in rendered
    assert "update docs" in rendered
    assert "pytest:focused" in rendered


def test_continuation_driver_runs_live_path_with_verifier_and_ledger(
    tmp_path: Path,
) -> None:
    controller, goal_store, db_path = _controller(tmp_path)
    ledger = SQLiteGoalRunStepLedger(db_path)
    driver = GoalContinuationDriver(
        controller=controller,
        goal_store=goal_store,
        ledger=ledger,
    )
    outcomes = iter(
        (
            GoalTurnResult(
                proposed_outcome="continue",
                reason="tests still failing",
                evidence_refs=("pytest:fail",),
                next_instruction="fix failure",
            ),
            GoalTurnResult(
                proposed_outcome="satisfied",
                reason="tests pass",
                evidence_refs=("pytest:pass",),
            ),
        )
    )

    final = driver.run_until_stop(
        session_id="sess-goal",
        goal_id="goal-run",
        turn_runner=lambda _prompt: next(outcomes),
        verifier=lambda _goal, _state, result: (
            _passed_verification() if result.proposed_outcome == "satisfied" else None
        ),
    )

    steps = ledger.list_for_run(final.run_id)
    assert final.status == MissionStatus.COMPLETED
    assert final.turn_count == 2
    assert [step.evaluator_outcome for step in steps] == ["continue", "satisfied"]
    assert steps[0].tool_evidence_refs == ("pytest:fail",)
    assert final.proof_packet_ref == "awrk-proof:" + final.run_id


def test_driver_stops_on_caps_without_recursing_forever(tmp_path: Path) -> None:
    controller, goal_store, db_path = _controller(tmp_path)
    driver = GoalContinuationDriver(
        controller=controller,
        goal_store=goal_store,
        ledger=SQLiteGoalRunStepLedger(db_path),
    )

    final = driver.run_until_stop(
        session_id="sess-goal",
        goal_id="goal-run",
        caps=GoalRunCaps(max_auto_turns=1),
        turn_runner=lambda _prompt: GoalTurnResult(
            proposed_outcome="continue",
            reason="still working",
            next_instruction="keep going",
        ),
    )

    assert final.status == MissionStatus.PAUSED
    assert final.turn_count == 1
    assert final.last_evaluator_reason == "still working"


def test_async_wake_child_task_and_learning_candidate_are_review_gated(
    tmp_path: Path,
) -> None:
    controller, _goal_store, db_path = _controller(tmp_path)
    state = controller.run_replay(
        session_id="sess-goal",
        goal_id="goal-run",
        evaluations=(),
    )
    assert state.status == MissionStatus.PAUSED
    ledger = SQLiteGoalRunStepLedger(db_path)

    resumed = resume_goal_after_async_wake(
        controller=controller,
        ledger=ledger,
        session_id="sess-goal",
        reason="job_complete",
        evidence_refs=("job:1",),
    )

    assert resumed is not None
    assert resumed.active is True
    assert ledger.summary_for_run(resumed.run_id).evidence_refs == ("job:1",)

    child_step = build_child_task_step(
        state=resumed,
        task_id="child-1",
        role="reviewer",
        summary="checked docs",
        evidence_refs=("child:summary",),
    )
    assert child_step.evaluator_outcome == "continue"
    assert child_step.mission_status == MissionStatus.ACTIVE

    candidate = build_learning_candidate(state=state, proof_ref="proof:1")
    assert candidate["review_required"] is True
    assert candidate["proof_ref"] == "proof:1"


def test_goal_cli_live_driver_inspect_evidence_pause_resume(tmp_path: Path) -> None:
    db_path = tmp_path / "brain.db"
    goal_store: GoalStore = SQLiteGoalStore(db_path)
    goal_store.create(_goal())
    goal_store.bind_to_session("goal-run", "sess-goal")

    tone, output = execute_goal_cli_command(
        "/goal run goal-run --live continue:need-tests,satisfied:tests-pass",
        session_id="sess-goal",
        db_path=db_path,
    )
    assert tone == "success"
    assert "status=completed" in output
    assert "turns=2/3" in output

    tone, evidence = execute_goal_cli_command(
        "/goal evidence",
        session_id="sess-goal",
        db_path=db_path,
    )
    assert tone == "info"
    assert "continue" in evidence
    assert "satisfied" in evidence

    execute_goal_cli_command(
        "/goal run goal-run",
        session_id="sess-goal",
        db_path=db_path,
    )
    tone, paused = execute_goal_cli_command(
        "/goal pause",
        session_id="sess-goal",
        db_path=db_path,
    )
    assert tone == "success"
    assert "status=paused" in paused

    tone, resumed = execute_goal_cli_command(
        "/goal resume",
        session_id="sess-goal",
        db_path=db_path,
    )
    assert tone == "success"
    assert "status=active" in resumed

    tone, inspected = execute_goal_cli_command(
        "/goal inspect",
        session_id="sess-goal",
        db_path=db_path,
    )
    assert tone == "info"
    assert "ledger_steps=" in inspected
