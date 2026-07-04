"""Live continuation driver for goal-centered completion."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openminion.modules.brain.constants import MissionStatus
from openminion.modules.brain.schemas.goals import Goal
from openminion.modules.brain.storage.goals import GoalStore

from .clock import goal_now_ms
from .context import build_goal_context_card, render_goal_context_card
from .evaluator import GoalLiveEvaluationInput, GoalLiveEvaluator, GoalTurnResult
from .ledger import GoalRunStep, SQLiteGoalRunStepLedger
from .loop import GoalRunCaps, GoalRunController, GoalRunState
from .verification import GoalVerificationResult

TurnRunner = Callable[[str], GoalTurnResult]
Verifier = Callable[[Goal, GoalRunState, GoalTurnResult], GoalVerificationResult | None]


@dataclass
class GoalContinuationDriver:
    """Run bounded live goal continuations through an explicit runner seam."""

    controller: GoalRunController
    goal_store: GoalStore
    ledger: SQLiteGoalRunStepLedger
    evaluator: GoalLiveEvaluator = field(default_factory=GoalLiveEvaluator)
    _active_sessions: set[str] = field(default_factory=set)

    def run_until_stop(
        self,
        *,
        session_id: str,
        goal_id: str,
        turn_runner: TurnRunner,
        caps: GoalRunCaps | None = None,
        verifier: Verifier | None = None,
    ) -> GoalRunState:
        if session_id in self._active_sessions:
            raise RuntimeError(f"goal continuation already active: {session_id}")
        self._active_sessions.add(session_id)
        try:
            state = self.controller.start_goal_run(
                session_id=session_id,
                goal_id=goal_id,
                caps=caps,
            )
            return self._continue_active_run(
                state=state,
                turn_runner=turn_runner,
                verifier=verifier,
            )
        finally:
            self._active_sessions.discard(session_id)

    def resume_until_stop(
        self,
        *,
        state: GoalRunState,
        turn_runner: TurnRunner,
        verifier: Verifier | None = None,
    ) -> GoalRunState:
        if state.session_id in self._active_sessions:
            raise RuntimeError(f"goal continuation already active: {state.session_id}")
        self._active_sessions.add(state.session_id)
        try:
            return self._continue_active_run(
                state=state,
                turn_runner=turn_runner,
                verifier=verifier,
            )
        finally:
            self._active_sessions.discard(state.session_id)

    def _continue_active_run(
        self,
        *,
        state: GoalRunState,
        turn_runner: TurnRunner,
        verifier: Verifier | None,
    ) -> GoalRunState:
        current = state
        while current.active:
            goal = self._goal(current.goal_id)
            summary = self.ledger.summary_for_run(current.run_id)
            card = build_goal_context_card(goal=goal, state=current, summary=summary)
            prompt = render_goal_context_card(card)
            started_at_ms = goal_now_ms()
            result = turn_runner(prompt)
            verification = verifier(goal, current, result) if verifier else None
            evaluation = self.evaluator.evaluate(
                GoalLiveEvaluationInput(
                    goal_id=current.goal_id,
                    turn_result=result,
                    verification=verification,
                )
            )
            updated, decision = self.controller.record_evaluation(current, evaluation)
            self.ledger.append(
                GoalRunStep(
                    run_id=updated.run_id,
                    session_id=updated.session_id,
                    goal_id=updated.goal_id,
                    turn_index=updated.turn_count,
                    started_at_ms=started_at_ms,
                    ended_at_ms=goal_now_ms(),
                    prompt_ref=f"goal-card:{updated.run_id}:{updated.turn_count}",
                    action_summary=result.reason,
                    tool_evidence_refs=result.evidence_refs,
                    verification_summary=_verification_summary(verification),
                    evaluator_outcome=evaluation.outcome,
                    mission_status=evaluation.mission_status,
                    evaluator_reason=evaluation.reason,
                    next_instruction=evaluation.next_instruction,
                    error_refs=result.error_refs,
                    autonomy_run_id=updated.run_id,
                    proof_ref=updated.proof_packet_ref or "",
                )
            )
            current = updated
            if not decision.should_continue:
                return current
        return current

    def _goal(self, goal_id: str) -> Goal:
        goal = self.goal_store.get(goal_id)
        if goal is None:
            raise KeyError(f"Unknown goal_id: {goal_id!r}")
        return goal


def resume_goal_after_async_wake(
    *,
    controller: GoalRunController,
    ledger: SQLiteGoalRunStepLedger,
    session_id: str,
    reason: str,
    evidence_refs: tuple[str, ...] = (),
) -> GoalRunState | None:
    """Reactivate a paused/async session run and record wake evidence."""

    resumed = controller.resume_session_run(session_id=session_id, reason=reason)
    if resumed is None:
        return None
    ledger.append_wake_event(
        run_id=resumed.run_id,
        session_id=resumed.session_id,
        goal_id=resumed.goal_id,
        reason=reason,
        evidence_refs=evidence_refs,
    )
    return resumed


def build_child_task_step(
    *,
    state: GoalRunState,
    task_id: str,
    role: str,
    summary: str,
    evidence_refs: tuple[str, ...] = (),
) -> GoalRunStep:
    """Represent a child task summary without completing the parent goal."""

    now = goal_now_ms()
    return GoalRunStep(
        run_id=state.run_id,
        session_id=state.session_id,
        goal_id=state.goal_id,
        turn_index=state.turn_count,
        started_at_ms=now,
        ended_at_ms=now,
        prompt_ref=f"child-task:{task_id}",
        action_summary=f"child task {role}: {summary}",
        tool_evidence_refs=evidence_refs,
        evaluator_outcome="continue",
        mission_status=MissionStatus.ACTIVE,
        evaluator_reason="child_task_summary_ingested",
        next_instruction="continue parent goal with child evidence",
    )


def build_learning_candidate(
    *,
    state: GoalRunState,
    proof_ref: str | None,
) -> dict[str, Any]:
    """Return a review-gated learning candidate, never trusted memory."""

    return {
        "kind": "strategy_outcome",
        "goal_id": state.goal_id,
        "run_id": state.run_id,
        "status": state.status.value,
        "proof_ref": proof_ref or state.proof_packet_ref or "",
        "review_required": True,
    }


def _verification_summary(verification: GoalVerificationResult | None) -> str:
    if verification is None:
        return "not_checked"
    return (
        f"status={verification.status}; "
        f"unmet={len(verification.unmet_criteria)}; "
        f"missing={len(verification.missing_deliverables)}; "
        f"failures={len(verification.triggered_failures)}"
    )


__all__ = [
    "GoalContinuationDriver",
    "TurnRunner",
    "Verifier",
    "build_child_task_step",
    "build_learning_candidate",
    "resume_goal_after_async_wake",
]
