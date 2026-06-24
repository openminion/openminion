"""Approval-decision helpers for the developer API."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from openminion.api.config import close_api_runtime_if_owned
from openminion.api.core.deps import resolve_runtime_manager

APPROVAL_CHOICES: tuple[str, ...] = (
    "allow_once",
    "allow_session",
    "allow_forever",
    "deny",
)


def parse_decision(raw: Any) -> str | None:
    """Return the matching typed approval decision, if any."""
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().lower()
    if not normalized:
        return None
    return normalized if normalized in APPROVAL_CHOICES else None


def _invalid_decision_error(raw: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "INVALID_DECISION",
            "message": (
                "approval decision must be one of: " + ", ".join(APPROVAL_CHOICES)
            ),
            "details": {
                "received": raw
                if isinstance(raw, (str, int, float, bool, type(None)))
                else repr(raw),
                "choices": list(APPROVAL_CHOICES),
            },
        },
    }


def _missing_field_error(field: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "INVALID_REQUEST",
            "message": f"missing required field: {field}",
            "details": {"field": field},
        },
    }


def process_approval_decision(
    *,
    config_path: Optional[str],
    runtime: Any,
    body: Mapping[str, Any],
) -> dict[str, Any]:
    """Process a typed approval decision and create the matching grant."""
    if not isinstance(body, Mapping):
        return _missing_field_error("body")

    approval_id_raw = body.get("approval_id")
    if not isinstance(approval_id_raw, str) or not approval_id_raw.strip():
        return _missing_field_error("approval_id")
    approval_id = approval_id_raw.strip()

    decision = parse_decision(body.get("decision"))
    if decision is None:
        return _invalid_decision_error(body.get("decision"))

    invocation = body.get("invocation")
    if not isinstance(invocation, Mapping):
        return _missing_field_error("invocation")
    ctx = body.get("ctx")
    if not isinstance(ctx, Mapping):
        return _missing_field_error("ctx")

    _, active_runtime, own_runtime = resolve_runtime_manager(
        config_path=config_path,
        runtime=runtime,
    )
    try:
        policyctl = _resolve_policyctl(active_runtime)
        if policyctl is None:
            return {
                "ok": False,
                "error": {
                    "code": "POLICY_UNAVAILABLE",
                    "message": "runtime has no PolicyCtl; cannot create grant",
                    "details": {"approval_id": approval_id},
                },
            }
        grant_id = policyctl.create_grant_from_confirmation(
            invocation=dict(invocation),
            ctx=dict(ctx),
            action=decision,
        )
        return {
            "ok": True,
            "approval_id": approval_id,
            "decision": decision,
            "grant_id": grant_id,
        }
    finally:
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)


def _resolve_policyctl(runtime: Any) -> Any:
    """Return the runtime PolicyCtl when one is exposed."""
    for attr in ("policyctl", "policy_ctl", "policy", "action_policy"):
        candidate = getattr(runtime, attr, None)
        if candidate is None:
            continue
        if hasattr(candidate, "create_grant_from_confirmation"):
            return candidate
    return None


__all__ = [
    "APPROVAL_CHOICES",
    "parse_decision",
    "process_approval_decision",
]
