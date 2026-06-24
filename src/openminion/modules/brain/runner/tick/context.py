"""Tick-run context and confirmation state for the brain runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from ...constants import (
    BRAIN_CONFIRM_RESPONSE_AFFIRM,
    BRAIN_CONFIRM_RESPONSE_DENY,
    BRAIN_CONFIRM_RESPONSE_UNCLEAR,
    BRAIN_DECISION_ROUTE_ACT,
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
        "mode_name": getattr(state, "active_mode_name", None),
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
    state.pending_confirmation_sub_intents = list(
        getattr(state, "decision_sub_intents", []) or []
    )
    state.pending_confirmation_sub_intent_refs = list(
        getattr(state, "decision_sub_intent_refs", []) or []
    )
    state.pending_confirmation_goal = (
        str(getattr(state, "goal", "") or "").strip() or None
    )
    state.pending_confirmation_last_user_input = str(
        getattr(state, "last_user_input", "") or ""
    ).strip()
    state.pending_confirmation_rationale = str(
        getattr(state, "decision_rationale", "") or ""
    ).strip()
    criteria = getattr(state, "decision_success_criteria", {})
    state.pending_confirmation_success_criteria = (
        dict(criteria) if isinstance(criteria, dict) else {}
    )
    feasibility_state = getattr(state, "decision_feasibility_state", {})
    state.pending_confirmation_feasibility_state = (
        dict(feasibility_state) if isinstance(feasibility_state, dict) else {}
    )
    state.pending_confirmation_feasibility_report = getattr(
        state,
        "decision_feasibility_report",
        None,
    )


def _apply_pending_confirmation_metadata_for_replay(state: Any) -> None:
    pending_goal = str(getattr(state, "pending_confirmation_goal", "") or "").strip()
    if pending_goal:
        state.goal = pending_goal
    pending_last_user_input = str(
        getattr(state, "pending_confirmation_last_user_input", "") or ""
    ).strip()
    if pending_last_user_input:
        state.last_user_input = pending_last_user_input
    state.decision_sub_intents = list(
        getattr(state, "pending_confirmation_sub_intents", []) or []
    )
    state.decision_sub_intent_refs = list(
        getattr(state, "pending_confirmation_sub_intent_refs", []) or []
    )
    state.decision_rationale = str(
        getattr(state, "pending_confirmation_rationale", "") or ""
    ).strip()
    pending_criteria = getattr(state, "pending_confirmation_success_criteria", {})
    if isinstance(pending_criteria, dict) and pending_criteria:
        state.decision_success_criteria = dict(pending_criteria)
    else:
        plan = getattr(state, "plan", None)
        plan_criteria = getattr(plan, "success_criteria", None)
        state.decision_success_criteria = (
            dict(plan_criteria) if isinstance(plan_criteria, dict) else {}
        )
    pending_feasibility = getattr(state, "pending_confirmation_feasibility_state", {})
    state.decision_feasibility_state = (
        dict(pending_feasibility) if isinstance(pending_feasibility, dict) else {}
    )
    from ...execution.feasibility import extract_feasibility_report

    state.decision_feasibility_report = extract_feasibility_report(
        state.decision_feasibility_state
    )


def _default_decision() -> Any:
    return SimpleNamespace(
        mode=BRAIN_DECISION_ROUTE_ACT,
        reason_code="",
        confidence=0.5,
        sub_intents=[],
        rationale="",
        _seeded_commands=[],
        question=None,
        answer=None,
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
    decision: Any = field(default_factory=_default_decision)


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
