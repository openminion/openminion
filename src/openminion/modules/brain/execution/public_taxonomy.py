from typing import Any

from ..constants import (
    BRAIN_INTERNAL_MODE_ACT_ADAPTIVE,
    BRAIN_INTERNAL_MODE_ACT_CODING,
    BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE,
    BRAIN_INTERNAL_MODE_ACT_RESEARCH,
    BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
    BRAIN_INTERNAL_MODE_LOOP_PHASE_EVAL,
    BRAIN_INTERNAL_MODE_LOOP_PHASE_OBSERVE,
    BRAIN_INTERNAL_MODE_LOOP_PHASE_REFINE,
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_ACT_PROFILE_GENERAL,
    BRAIN_ACT_PROFILE_ORCHESTRATE,
    BRAIN_ACT_PROFILE_RESEARCH,
    BRAIN_DECISION_ROUTE_ACT,
    BRAIN_DECISION_ROUTE_RESPOND,
    BRAIN_EXECUTION_TARGET_DELEGATED,
)

_PUBLIC_MODE_PAYLOADS: dict[str, dict[str, str]] = {
    BRAIN_DECISION_ROUTE_RESPOND: {"mode_name": BRAIN_DECISION_ROUTE_RESPOND},
    BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE: {
        "mode_name": BRAIN_DECISION_ROUTE_ACT,
        "act_profile": BRAIN_ACT_PROFILE_ORCHESTRATE,
    },
    BRAIN_INTERNAL_MODE_ACT_ADAPTIVE: {
        "mode_name": BRAIN_DECISION_ROUTE_ACT,
        "act_profile": BRAIN_ACT_PROFILE_GENERAL,
    },
    BRAIN_INTERNAL_MODE_ACT_CODING: {
        "mode_name": BRAIN_DECISION_ROUTE_ACT,
        "act_profile": BRAIN_ACT_PROFILE_CODING,
    },
    BRAIN_INTERNAL_MODE_ACT_RESEARCH: {
        "mode_name": BRAIN_DECISION_ROUTE_ACT,
        "act_profile": BRAIN_ACT_PROFILE_RESEARCH,
    },
    BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED: {
        "mode_name": BRAIN_DECISION_ROUTE_ACT,
        "execution_target": BRAIN_EXECUTION_TARGET_DELEGATED,
    },
    BRAIN_INTERNAL_MODE_LOOP_PHASE_OBSERVE: {
        "mode_name": BRAIN_DECISION_ROUTE_ACT,
        "loop_phase": "observe",
    },
    BRAIN_INTERNAL_MODE_LOOP_PHASE_EVAL: {
        "mode_name": BRAIN_DECISION_ROUTE_ACT,
        "loop_phase": "eval",
    },
    BRAIN_INTERNAL_MODE_LOOP_PHASE_REFINE: {
        "mode_name": BRAIN_DECISION_ROUTE_ACT,
        "loop_phase": "refine",
    },
}


def public_surface_payload_for_mode_name(mode_name: str | None) -> dict[str, Any]:
    normalized = str(mode_name or "").strip().lower()
    if not normalized:
        return {}
    payload = _PUBLIC_MODE_PAYLOADS.get(normalized)
    if payload is not None:
        return dict(payload)
    return {"mode_name": normalized}


def public_surface_payload_for_state(
    state: Any | None,
    *,
    mode_name: str | None = None,
) -> dict[str, Any]:
    payload = public_surface_payload_for_mode_name(
        mode_name or str(getattr(state, "active_mode_name", "") or "").strip() or None
    )
    working_profile = str(getattr(state, "working_act_profile", "") or "").strip()
    if working_profile:
        payload["act_profile"] = working_profile
    working_execution_target = str(
        getattr(state, "working_execution_target_kind", "") or ""
    ).strip()
    if working_execution_target:
        payload["execution_target"] = working_execution_target
    return payload


def public_mode_name_for_mode_name(mode_name: str | None) -> str | None:
    return (
        str(
            public_surface_payload_for_mode_name(mode_name).get("mode_name") or ""
        ).strip()
        or None
    )


def public_mode_name_for_state(
    state: Any | None, *, mode_name: str | None = None
) -> str | None:
    return (
        str(
            public_surface_payload_for_state(state, mode_name=mode_name).get(
                "mode_name"
            )
            or ""
        ).strip()
        or None
    )
