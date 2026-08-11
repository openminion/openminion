"""Request-match-field helpers for context retrieval ranking."""

from typing import Any

from ..schemas import BuildPackRequest


def _structural_values(values: Any) -> set[str]:
    return {
        str(item).strip().lower() for item in list(values or []) if str(item).strip()
    }


def _normalized_value(overlay: dict[str, Any], key: str) -> str:
    return str(overlay.get(key) or "").strip().lower()


def request_decision_match_fields(request: BuildPackRequest) -> dict[str, Any]:
    overlay = dict(request.live_state_overlay or {})
    return {
        "reason_code": _normalized_value(overlay, "decision_reason_code"),
        "sub_intents": _structural_values(overlay.get("decision_sub_intents")),
        "act_profile": _normalized_value(overlay, "working_act_profile"),
        "execution_target_kind": _normalized_value(
            overlay, "working_execution_target_kind"
        ),
        "target_agent_id": _normalized_value(overlay, "delegation_target_agent_id"),
    }


def request_improvement_note_match_fields(request: BuildPackRequest) -> dict[str, Any]:
    overlay = dict(request.live_state_overlay or {})
    return {
        "tool_tags": _structural_values(overlay.get("improvement_note_tool_tags")),
        "error_tags": _structural_values(overlay.get("improvement_note_error_tags")),
    }


def request_strategy_outcome_match_fields(request: BuildPackRequest) -> dict[str, Any]:
    overlay = dict(request.live_state_overlay or {})
    return {
        "strategy_id": _normalized_value(overlay, "strategy_outcome_strategy_id"),
        "capability_category": _normalized_value(
            overlay, "strategy_outcome_capability_category"
        ),
        "intent_category": _normalized_value(
            overlay, "strategy_outcome_intent_category"
        ),
    }


def request_post_completion_critique_match_fields(
    request: BuildPackRequest,
) -> dict[str, Any]:
    overlay = dict(request.live_state_overlay or {})
    return {
        "intent_ids": _structural_values(
            overlay.get("post_completion_critique_intent_ids")
        ),
        "sub_intents": _structural_values(
            overlay.get("post_completion_critique_sub_intents")
        ),
        "route_chosen": _normalized_value(overlay, "post_completion_critique_route"),
    }
