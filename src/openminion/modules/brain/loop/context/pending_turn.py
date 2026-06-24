from collections.abc import Mapping
from typing import Any

from ...schemas import Decision, PendingTurnContext, WorkingState

PENDING_TURN_CONTEXT_MAX_STALE_TURNS = 3


def _pending_turn_context_field_was_provided(
    decision: Decision | object | None,
) -> bool:
    raw = getattr(decision, "model_fields_set", None)
    if not isinstance(raw, set | frozenset):
        return False
    return "pending_turn_context" in {str(item) for item in raw}


def _pending_turn_context_snapshot(
    state_inline: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, int]:
    if not isinstance(state_inline, Mapping):
        return None, 0
    raw = state_inline.get("pending_turn_context")
    if not isinstance(raw, Mapping) or not raw:
        return None, 0
    try:
        stale_turns = int(state_inline.get("pending_turn_context_stale_turns", 0) or 0)
    except Exception:  # noqa: BLE001
        stale_turns = 0
    return dict(raw), stale_turns


def preserve_pending_turn_context_on_new_input(
    *,
    state_inline: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, int]:
    pending_turn_context, stale_turns = _pending_turn_context_snapshot(state_inline)
    if pending_turn_context is None:
        return None, 0
    next_stale_turns = stale_turns + 1
    if next_stale_turns > PENDING_TURN_CONTEXT_MAX_STALE_TURNS:
        return None, 0
    return pending_turn_context, next_stale_turns


def pending_turn_context_for_prompt(
    *,
    state_inline: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    pending_turn_context, stale_turns = _pending_turn_context_snapshot(state_inline)
    if pending_turn_context is None:
        return None
    if stale_turns > PENDING_TURN_CONTEXT_MAX_STALE_TURNS:
        return None
    return pending_turn_context


def sync_pending_turn_context_from_decision(
    *,
    state: WorkingState,
    decision: Decision | object | None,
    user_input: str | None,
) -> None:
    del user_input
    if not _pending_turn_context_field_was_provided(decision):
        return
    replacement = getattr(decision, "pending_turn_context", None)
    if isinstance(replacement, PendingTurnContext):
        state.pending_turn_context = replacement.model_copy(deep=True)
        state.pending_turn_context_stale_turns = 0
        return
    state.pending_turn_context = None
    state.pending_turn_context_stale_turns = 0
