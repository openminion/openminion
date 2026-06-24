from typing import Any

from ..schemas import WorkingState

_BASE_LIVE_STATE_FIELDS = {
    "goal",
    "status",
    "last_result",
    "step_outputs",
    "intent_execution_states",
}


def build_live_state_overlay(
    *,
    state: WorkingState,
    extra_fields: set[str] | None = None,
) -> dict[str, Any]:
    include_fields = _BASE_LIVE_STATE_FIELDS | set(extra_fields or ())
    return state.model_dump(mode="json", include=include_fields)


def intent_execution_payload(state: WorkingState) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for item in getattr(state, "intent_execution_states", []) or []:
        try:
            payload.append(item.model_dump(mode="json"))
        except Exception:
            continue
    return payload
