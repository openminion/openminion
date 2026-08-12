from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .runtime.turn import post_action as _post_action_runtime
from ..constants import BRAIN_STATE_WAITING_USER
from ..diagnostics.transitions import transition
from ..diagnostics.events import CanonicalEventLogger
from ..schemas import ActionResult, Command, PostActionJudgment, WorkingState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..runner import BrainRunner


def clear_post_action_user_message(*, state: WorkingState) -> None:
    state.post_action_user_message = ""


def _consume_waiting_user_state_before_post_action_transition(
    *, state: WorkingState
) -> None:
    if state.status != BRAIN_STATE_WAITING_USER:
        return
    transition(state, "user_input_received")


def evaluate_post_action_judgment(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    logger: CanonicalEventLogger,
    fact_kind: str,
    action_result: ActionResult | None = None,
    current_command: Command | None = None,
    current_step_index: int | None = None,
    total_steps: int | None = None,
    runtime_facts: dict[str, Any] | None = None,
) -> PostActionJudgment | None:
    return _post_action_runtime.evaluate_post_action_judgment(
        runner,
        state=state,
        logger=logger,
        fact_kind=fact_kind,
        action_result=action_result,
        current_command=current_command,
        current_step_index=current_step_index,
        total_steps=total_steps,
        runtime_facts=runtime_facts,
    )


def apply_post_action_judgment(
    *,
    state: WorkingState,
    judgment: PostActionJudgment,
    step_key: str,
    total_steps: int,
    max_retries_per_step: int,
    transition_to_replan: Callable[..., bool] | None,
) -> str:
    clear_post_action_user_message(state=state)
    message = (judgment.user_message or judgment.reason).strip()
    reason = judgment.reason.strip()
    outcome = str(judgment.outcome).strip()
    if message:
        state.post_action_user_message = message

    if outcome in {"advance", "skip"}:
        _consume_waiting_user_state_before_post_action_transition(state=state)
        state.cursor += 1
        if step_key:
            state.retries_for_step.pop(step_key, None)
        transition(
            state,
            "task_completed" if state.cursor >= total_steps else "step_advanced",
        )
        return outcome

    if outcome == "retry":
        _consume_waiting_user_state_before_post_action_transition(state=state)
        retry_count = state.retries_for_step.get(step_key, 0) + 1 if step_key else 0
        if step_key:
            state.retries_for_step[step_key] = retry_count
        if step_key and retry_count > max_retries_per_step:
            transition(state, "retries_exhausted")
        else:
            transition(state, "step_retrying")
        return outcome

    if outcome == "replan":
        _consume_waiting_user_state_before_post_action_transition(state=state)
        if transition_to_replan is not None and transition_to_replan(
            reason=reason or "llm_post_action_replan"
        ):
            return outcome
        transition(state, "feasibility_needs_user")
        return outcome

    if outcome == "halt":
        transition(state, "execution_stopped")
        return outcome

    if outcome == "ask_user":
        _consume_waiting_user_state_before_post_action_transition(state=state)
        transition(state, "judgment_ask_user")
        return outcome

    raise ValueError(f"unsupported post-action outcome: {outcome}")
