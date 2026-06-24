"""Request-match-field helpers for context retrieval ranking."""

from typing import Any

from ..schemas import BuildPackRequest


def _structural_values(values: Any) -> set[str]:
    return {
        str(item).strip().lower() for item in list(values or []) if str(item).strip()
    }


def request_decision_match_fields(request: BuildPackRequest) -> dict[str, Any]:
    overlay = dict(request.live_state_overlay or {})
    return {
        "reason_code": str(overlay.get("decision_reason_code") or "").strip().lower(),
        "sub_intents": _structural_values(overlay.get("decision_sub_intents")),
        "act_profile": str(overlay.get("working_act_profile") or "").strip().lower(),
        "execution_target_kind": str(overlay.get("working_execution_target_kind") or "")
        .strip()
        .lower(),
        "target_agent_id": str(overlay.get("delegation_target_agent_id") or "")
        .strip()
        .lower(),
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
        "strategy_id": str(overlay.get("strategy_outcome_strategy_id") or "")
        .strip()
        .lower(),
        "capability_category": str(
            overlay.get("strategy_outcome_capability_category") or ""
        )
        .strip()
        .lower(),
        "intent_category": str(overlay.get("strategy_outcome_intent_category") or "")
        .strip()
        .lower(),
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
        "route_chosen": str(overlay.get("post_completion_critique_route") or "")
        .strip()
        .lower(),
    }
