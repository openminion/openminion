"""Tick-run context and confirmation state for the brain runner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ...constants import (
    BRAIN_CONFIRM_RESPONSE_AFFIRM,
    BRAIN_CONFIRM_RESPONSE_DENY,
    BRAIN_CONFIRM_RESPONSE_UNCLEAR,
)
from ...diagnostics.events import CanonicalEventLogger
from ...schemas import BudgetStopReason

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..core import BrainRunner


from ...execution.delegation import _runner_delegate  # noqa: F401


def _budget_exhaustion_message(reason: BudgetStopReason) -> str:
    if reason == BudgetStopReason.TIME_EXHAUSTED:
        return (
            "Turn time budget exhausted before this plan could finish. "
            "Narrow scope, split the task, or continue in a new turn."
        )
    return "Tick budget exhausted. Narrow scope or continue in a new turn."


def _grant_once_from_confirmation(
    runner: "BrainRunner",
    *,
    state,
    command,
    logger: CanonicalEventLogger,
) -> tuple[str | None, bool]:
    policy_api = getattr(runner, "policy_api", None)
    if policy_api is None:
        return None, False
    grant_once = getattr(policy_api, "grant_once_from_confirmation", None)
    if not callable(grant_once):
        return None, False
    session_context = {
        "session_id": state.session_id,
        "trace_id": state.trace_id,
        "constraints": state.constraints,
        "mode_name": state.active_mode_name,
    }
    try:
        grant_id = str(
            grant_once(
                command=command,
                working_state=state,
                session_context=session_context,
            )
            or ""
        ).strip()
    except Exception as exc:
        logger.emit(
            "brain.confirm_replay_grant_failed",
            {"error": type(exc).__name__},
            trace_id=state.trace_id,
        )
        return None, True
    if grant_id:
        logger.emit(
            "brain.confirm_replay_grant_created",
            {"grant_id": grant_id, "kind": command.kind},
            trace_id=state.trace_id,
        )
        return grant_id, True
    return None, True


def _parse_confirmation_response(runner: "BrainRunner", text: str) -> str:
    policy_api = getattr(runner, "policy_api", None)
    if policy_api is not None:
        parser = getattr(policy_api, "parse_confirmation_response", None)
        if callable(parser):
            try:
                result = str(parser(text) or "").strip().lower()
                if result in {
                    BRAIN_CONFIRM_RESPONSE_AFFIRM,
                    BRAIN_CONFIRM_RESPONSE_DENY,
                    BRAIN_CONFIRM_RESPONSE_UNCLEAR,
                }:
                    return result
            except Exception:
                pass
    try:
        from openminion.modules.policy.runtime.service import (
            parse_confirmation_response,
        )

        return str(parse_confirmation_response(text)).strip().lower()
    except Exception:
        return BRAIN_CONFIRM_RESPONSE_UNCLEAR


def _clear_pending_confirmation_metadata(state: Any) -> None:
    state.pending_confirmation_sub_intents = []
    state.pending_confirmation_sub_intent_refs = []
    state.pending_confirmation_goal = None
    state.pending_confirmation_last_user_input = ""
    state.pending_confirmation_rationale = ""
    state.pending_confirmation_success_criteria = {}
    state.pending_confirmation_feasibility_state = {}
    state.pending_confirmation_feasibility_report = None


def _store_pending_confirmation_metadata(state: Any) -> None:
    """Persist current decision metadata into the pending-confirmation slots."""
    state.pending_confirmation_sub_intents = list(state.decision_sub_intents)
    state.pending_confirmation_sub_intent_refs = list(state.decision_sub_intent_refs)
    state.pending_confirmation_goal = str(state.goal or "").strip() or None
    state.pending_confirmation_last_user_input = state.last_user_input.strip()
    state.pending_confirmation_rationale = state.decision_rationale.strip()
    state.pending_confirmation_success_criteria = dict(state.decision_success_criteria)
    state.pending_confirmation_feasibility_state = dict(
        state.decision_feasibility_state
    )
    state.pending_confirmation_feasibility_report = state.decision_feasibility_report


def _apply_pending_confirmation_metadata_for_replay(state: Any) -> None:
    pending_goal = str(state.pending_confirmation_goal or "").strip()
    if pending_goal:
        state.goal = pending_goal
    pending_last_user_input = state.pending_confirmation_last_user_input.strip()
    if pending_last_user_input:
        state.last_user_input = pending_last_user_input
    state.decision_sub_intents = list(state.pending_confirmation_sub_intents)
    state.decision_sub_intent_refs = list(state.pending_confirmation_sub_intent_refs)
    state.decision_rationale = state.pending_confirmation_rationale.strip()
    pending_criteria = state.pending_confirmation_success_criteria
    if pending_criteria:
        state.decision_success_criteria = dict(pending_criteria)
    else:
        state.decision_success_criteria = (
            dict(state.plan.success_criteria) if state.plan is not None else {}
        )
    state.decision_feasibility_state = dict(
        state.pending_confirmation_feasibility_state
    )
    from ...execution.feasibility import extract_feasibility_report

    state.decision_feasibility_report = extract_feasibility_report(
        state.decision_feasibility_state
    )


@dataclass
class TickRunContext:
    session_id: str
    user_input: str | None = None
    trace_id: str | None = None
    forced_tools: list[str] | None = None
    capability_category: str | None = None
    original_user_input: str | None = None
    has_new_user_input: bool = False
    mission_route: Any | None = None
    skip_initial_interpret: bool = False
    skip_initial_append: bool = False
    skip_decide: bool = False
    consume_user_input_for_command: bool = False
    mask_pending_confirmation_in_output: bool = False
    masked_resume_cursor: int | None = None
    forced_reset_policy_name: str | None = None
    decision: Any | None = None


def build_tick_run_context(
    *,
    session_id: str,
    user_input: str | None,
    trace_id: str | None,
    forced_tools: list[str] | None,
    capability_category: str | None,
) -> TickRunContext:
    return TickRunContext(
        session_id=session_id,
        user_input=user_input,
        trace_id=trace_id,
        forced_tools=forced_tools,
        capability_category=capability_category,
        original_user_input=user_input,
        has_new_user_input=bool(str(user_input or "").strip()),
    )
