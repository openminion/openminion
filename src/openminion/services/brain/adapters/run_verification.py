from collections.abc import Sequence

from openminion.modules.brain.runtime.verification.policy import (
    VerifierResult,
    is_run_completion_confirmed,
)
from openminion.modules.brain.schemas.goals import FailureCondition, Goal
from openminion.modules.storage.runtime.session_store import EventRecord, SessionStore
from openminion.modules.task.run import (
    RUN_TERMINAL_BLOCKED,
    RUN_TERMINAL_BUDGET_EXHAUSTED,
    RUN_TERMINAL_COMPLETED,
    RUN_TERMINAL_CANCELLED,
    RUN_TERMINAL_FAILED,
    RUN_TERMINAL_NEEDS_HUMAN,
    Run,
    RunCheckpoint,
    RunTerminalState,
    append_run_checkpoint_event,
    append_run_state_event,
    resolve_run_terminal_persistence,
)

TERMINAL_STATE_PROVENANCE_TYPED = "typed_verifier_reduction"
TERMINAL_STATE_PROVENANCE_FIELD = "terminal_state_provenance"

_FAILURE_KIND_TO_TERMINAL: dict[str, RunTerminalState] = {
    "deliverable_missing": RUN_TERMINAL_FAILED,
    "success_criterion_unmet": RUN_TERMINAL_FAILED,
    "budget_exhausted": RUN_TERMINAL_BUDGET_EXHAUSTED,
    "blocker_unresolved": RUN_TERMINAL_BLOCKED,
    "capability_boundary": RUN_TERMINAL_NEEDS_HUMAN,
    "operator_cancelled": RUN_TERMINAL_CANCELLED,
}


def derive_run_terminal_state(
    goal: Goal,
    verifier_results: Sequence[VerifierResult],
    *,
    fired_failure_conditions: Sequence[FailureCondition] = (),
) -> RunTerminalState:
    if fired_failure_conditions:
        return _FAILURE_KIND_TO_TERMINAL[fired_failure_conditions[0].kind]

    if is_run_completion_confirmed(goal=goal, results=list(verifier_results)):
        return RUN_TERMINAL_COMPLETED

    return RUN_TERMINAL_FAILED


def bind_run_terminal_event(
    *,
    run: Run,
    goal: Goal,
    verifier_results: Sequence[VerifierResult],
    sessions: SessionStore,
    fired_failure_conditions: Sequence[FailureCondition] = (),
    checkpoint_id: str,
    sequence: int = 0,
    created_at: str = "",
    current_step: str = "turn.completed",
    conversation_id: str | None = None,
    thread_id: str | None = None,
    attach_id: str | None = None,
    extra_payload: dict[str, object] | None = None,
    session_turn_fence_token: int | None = None,
) -> EventRecord:
    _validate_run_goal_binding(run=run, goal=goal)

    terminal_state = derive_run_terminal_state(
        goal,
        verifier_results,
        fired_failure_conditions=fired_failure_conditions,
    )
    persisted_state = resolve_run_terminal_persistence(terminal_state)

    checkpoint = RunCheckpoint(
        checkpoint_id=checkpoint_id,
        run_id=run.run_id,
        goal_id=goal.goal_id,
        sequence=sequence,
        state_snapshot=_build_checkpoint_snapshot(
            terminal_state=terminal_state,
            verifier_results=verifier_results,
            fired_failure_conditions=fired_failure_conditions,
        ),
        created_at=created_at,
    )
    append_run_checkpoint_event(
        sessions,
        session_id=run.session_id,
        checkpoint=checkpoint,
        conversation_id=conversation_id,
        thread_id=thread_id,
        session_turn_fence_token=session_turn_fence_token,
    )

    payload = _build_terminal_event_payload(
        checkpoint_id=checkpoint_id,
        goal=goal,
        terminal_state=terminal_state,
        verifier_results=verifier_results,
        fired_failure_conditions=fired_failure_conditions,
    )
    if extra_payload:
        for key, value in extra_payload.items():
            payload.setdefault(key, value)

    return append_run_state_event(
        sessions,
        session_id=run.session_id,
        run_id=run.run_id,
        state=persisted_state,
        current_step=current_step,
        payload=payload,
        conversation_id=conversation_id,
        thread_id=thread_id,
        attach_id=attach_id,
        session_turn_fence_token=session_turn_fence_token,
    )


def _validate_run_goal_binding(*, run: Run, goal: Goal) -> None:
    if not run.run_id:
        raise ValueError("Run.run_id is required for bind_run_terminal_event")
    if not run.goal_id:
        raise ValueError("Run.goal_id is required for bind_run_terminal_event")
    if run.goal_id != goal.goal_id:
        raise ValueError(
            f"Run.goal_id ({run.goal_id!r}) does not match Goal.goal_id "
            f"({goal.goal_id!r}); structural binding requires matching identifiers."
        )


def _build_checkpoint_snapshot(
    *,
    terminal_state: RunTerminalState,
    verifier_results: Sequence[VerifierResult],
    fired_failure_conditions: Sequence[FailureCondition],
) -> dict[str, object]:
    return {
        "terminal_state": terminal_state,
        TERMINAL_STATE_PROVENANCE_FIELD: TERMINAL_STATE_PROVENANCE_TYPED,
        "verifier_result_count": len(verifier_results),
        "verifier_results": [row.to_dict() for row in verifier_results],
        "fired_failure_condition_ids": [
            condition.condition_id for condition in fired_failure_conditions
        ],
    }


def _build_terminal_event_payload(
    *,
    checkpoint_id: str,
    goal: Goal,
    terminal_state: RunTerminalState,
    verifier_results: Sequence[VerifierResult],
    fired_failure_conditions: Sequence[FailureCondition],
) -> dict[str, object]:
    return {
        "terminal_state": terminal_state,
        TERMINAL_STATE_PROVENANCE_FIELD: TERMINAL_STATE_PROVENANCE_TYPED,
        "checkpoint_id": checkpoint_id,
        "verifier_result_count": len(verifier_results),
        "verifier_pass_count": sum(1 for row in verifier_results if row.passed),
        "verifier_fail_count": sum(1 for row in verifier_results if not row.passed),
        "fired_failure_condition_ids": [
            condition.condition_id for condition in fired_failure_conditions
        ],
        "goal_id": goal.goal_id,
    }


__all__ = [
    "TERMINAL_STATE_PROVENANCE_FIELD",
    "TERMINAL_STATE_PROVENANCE_TYPED",
    "bind_run_terminal_event",
    "derive_run_terminal_state",
]
