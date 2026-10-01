"""Approval-decision helpers for the developer API."""

from typing import Any, Mapping

from openminion.api.config import close_api_runtime_if_owned
from openminion.api.core.deps import resolve_runtime_manager
from openminion.base.time import utc_now_iso
from openminion.modules.policy.constants import (
    POLICY_APPROVAL_CHOICES as APPROVAL_CHOICES,
)
from openminion.modules.policy.models import PolicyControlError
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.ops.api import job_view
from openminion.tools.ops.service import OpsService


def parse_decision(raw: Any) -> str | None:
    """Return the matching typed approval decision, if any."""
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().lower()
    if not normalized:
        return None
    return normalized if normalized in APPROVAL_CHOICES else None


def _invalid_decision_error(
    raw: Any, *, choices: tuple[str, ...] = APPROVAL_CHOICES
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "INVALID_DECISION",
            "message": ("approval decision must be one of: " + ", ".join(choices)),
            "retryable": False,
            "details": {
                "received": raw
                if isinstance(raw, (str, int, float, bool, type(None)))
                else repr(raw),
                "choices": list(choices),
            },
        },
    }


def _missing_field_error(field: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "INVALID_REQUEST",
            "message": f"missing required field: {field}",
            "retryable": False,
            "details": {"field": field},
        },
    }


def process_approval_decision(
    *,
    config_path: str | None,
    runtime: Any,
    body: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve an exact active or server-owned approval request."""
    if not isinstance(body, Mapping):
        return _missing_field_error("body")

    approval_id_raw = body.get("approval_id")
    if not isinstance(approval_id_raw, str) or not approval_id_raw.strip():
        return _missing_field_error("approval_id")
    approval_id = approval_id_raw.strip()

    decision = parse_decision(body.get("decision"))
    if decision is None:
        return _invalid_decision_error(body.get("decision"))

    runtime_manager, active_runtime, own_runtime = resolve_runtime_manager(
        config_path=config_path,
        runtime=runtime,
    )
    try:
        if "session_id" in body or "trace_id" in body:
            return _resolve_active_approval(
                runtime_manager,
                body=body,
                approval_id=approval_id,
                decision=decision,
            )
        ops_service = getattr(active_runtime, "ops_service", None)
        if isinstance(ops_service, OpsService) and (
            ops_service.jobs.find_by_approval_id(approval_id) is not None
        ):
            if decision not in {"allow_once", "deny"}:
                return _invalid_decision_error(decision, choices=("allow_once", "deny"))
            return _continue_ops_approval(
                ops_service,
                approval_id=approval_id,
                decision=decision,
            )
        if _is_ops_invocation(body):
            if decision not in {"allow_once", "deny"}:
                return _invalid_decision_error(
                    decision,
                    choices=("allow_once", "deny"),
                )
            return {
                "ok": False,
                "error": {
                    "code": "OPS_APPROVAL_NOT_FOUND",
                    "message": "Pending command approval was not found.",
                    "retryable": False,
                    "details": {"approval_id": approval_id},
                },
            }
        return _resolve_server_confirmation(
            active_runtime,
            approval_id=approval_id,
            decision=decision,
        )
    finally:
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)


def _resolve_active_approval(
    runtime_manager: Any,
    *,
    body: Mapping[str, Any],
    approval_id: str,
    decision: str,
) -> dict[str, Any]:
    session_id = _required_string(body, "session_id")
    if session_id is None:
        return _missing_field_error("session_id")
    trace_id = _required_string(body, "trace_id")
    if trace_id is None:
        return _missing_field_error("trace_id")
    if decision not in {"allow_once", "deny"}:
        return _invalid_decision_error(decision, choices=("allow_once", "deny"))

    handle = runtime_manager.get_turn_handle(trace_id)
    resolved = bool(
        handle is not None
        and handle.session_id == session_id
        and handle.resolve_approval(
            approval_id=approval_id,
            decision=decision,
        )
    )
    if not resolved:
        return {
            "ok": False,
            "error": {
                "code": "APPROVAL_NOT_ACTIVE",
                "message": "The approval request is not active.",
                "retryable": False,
                "details": {
                    "session_id": session_id,
                    "trace_id": trace_id,
                    "approval_id": approval_id,
                },
            },
        }
    return {
        "ok": True,
        "session_id": session_id,
        "trace_id": trace_id,
        "approval_id": approval_id,
        "decision": decision,
        "outcome": "applied" if decision == "allow_once" else "denied",
        "resolved_at": utc_now_iso(),
    }


def _required_string(body: Mapping[str, Any], field: str) -> str | None:
    value = body.get(field)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _is_ops_invocation(body: Mapping[str, Any]) -> bool:
    invocation = body.get("invocation")
    if not isinstance(invocation, Mapping):
        return False
    tool = str(invocation.get("tool", "") or "")
    method = str(invocation.get("method", "") or "")
    if not method and "." in tool:
        tool, method = tool.rsplit(".", 1)
    return (tool, method) == ("ops.command", "run")


def _continue_ops_approval(
    ops_service: OpsService, *, approval_id: str, decision: str
) -> dict[str, Any]:
    try:
        job = ops_service.continue_command_approval(
            approval_id=approval_id,
            decision=decision,
        )
    except PolicyControlError as exc:
        return _policy_error(exc, approval_id=approval_id)
    except (KeyError, PermissionError, ValueError) as exc:
        return {
            "ok": False,
            "error": {
                "code": "OPS_APPROVAL_STALE",
                "message": str(exc),
                "retryable": False,
                "details": {"approval_id": approval_id},
            },
        }
    except ToolRuntimeError as exc:
        return {
            "ok": False,
            "error": {
                "code": exc.code,
                "message": str(exc),
                "retryable": False,
                "details": dict(exc.details),
            },
        }
    return {
        "ok": True,
        "approval_id": approval_id,
        "decision": decision,
        "job": job_view(job),
    }


def _resolve_server_confirmation(
    active_runtime: Any,
    *,
    approval_id: str,
    decision: str,
) -> dict[str, Any]:
    policyctl = getattr(active_runtime, "action_policy", None)
    if policyctl is None:
        return {
            "ok": False,
            "error": {
                "code": "POLICY_UNAVAILABLE",
                "message": "runtime has no PolicyCtl; cannot resolve approval",
                "retryable": False,
                "details": {"approval_id": approval_id},
            },
        }
    if decision not in {"allow_once", "deny"}:
        return _invalid_decision_error(decision, choices=("allow_once", "deny"))
    try:
        grant_id = policyctl.resolve_confirmation(approval_id, decision)
    except PolicyControlError as exc:
        return _policy_error(exc, approval_id=approval_id)
    return {
        "ok": True,
        "approval_id": approval_id,
        "decision": decision,
        "grant_id": grant_id,
    }


def _policy_error(exc: PolicyControlError, *, approval_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": exc.code,
            "message": str(exc),
            "retryable": False,
            "details": {"approval_id": approval_id},
        },
    }


__all__ = [
    "APPROVAL_CHOICES",
    "parse_decision",
    "process_approval_decision",
]
