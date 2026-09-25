"""Approval-decision helpers for the developer API."""

from typing import Any, Mapping

from openminion.api.config import close_api_runtime_if_owned
from openminion.api.core.deps import resolve_runtime_manager
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
            "details": {"field": field},
        },
    }


def process_approval_decision(
    *,
    config_path: str | None,
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

    _, active_runtime, own_runtime = resolve_runtime_manager(
        config_path=config_path,
        runtime=runtime,
    )
    try:
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
        return _resolve_policy_decision(
            active_runtime,
            body=body,
            approval_id=approval_id,
            decision=decision,
        )
    finally:
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)


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
                "details": {"approval_id": approval_id},
            },
        }
    except ToolRuntimeError as exc:
        return {
            "ok": False,
            "error": {
                "code": exc.code,
                "message": str(exc),
                "details": dict(exc.details),
            },
        }
    return {
        "ok": True,
        "approval_id": approval_id,
        "decision": decision,
        "job": job_view(job),
    }


def _resolve_policy_decision(
    active_runtime: Any,
    *,
    body: Mapping[str, Any],
    approval_id: str,
    decision: str,
) -> dict[str, Any]:
    invocation = body.get("invocation")
    if not isinstance(invocation, Mapping):
        return _missing_field_error("invocation")
    ctx = body.get("ctx")
    if not isinstance(ctx, Mapping):
        return _missing_field_error("ctx")
    policyctl = getattr(active_runtime, "action_policy", None)
    if policyctl is None:
        return {
            "ok": False,
            "error": {
                "code": "POLICY_UNAVAILABLE",
                "message": "runtime has no PolicyCtl; cannot create grant",
                "details": {"approval_id": approval_id},
            },
        }
    tool = str(invocation.get("tool", "") or "")
    method = str(invocation.get("method", "") or "")
    if not method and "." in tool:
        tool, method = tool.rsplit(".", 1)
    exact_pending = (tool, method) in {
        ("blockchain", "send_transaction"),
        ("ops.command", "run"),
    }
    if exact_pending and decision not in {"allow_once", "deny"}:
        return _invalid_decision_error(decision, choices=("allow_once", "deny"))
    if (tool, method) == ("ops.command", "run"):
        return {
            "ok": False,
            "error": {
                "code": "OPS_APPROVAL_NOT_FOUND",
                "message": "Pending command approval was not found.",
                "details": {"approval_id": approval_id},
            },
        }
    if exact_pending:
        try:
            grant_id = policyctl.resolve_confirmation(approval_id, decision)
        except PolicyControlError as exc:
            return _policy_error(exc, approval_id=approval_id)
    elif decision == "deny":
        grant_id = None
    else:
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


def _policy_error(exc: PolicyControlError, *, approval_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": exc.code,
            "message": str(exc),
            "details": {"approval_id": approval_id},
        },
    }


__all__ = [
    "APPROVAL_CHOICES",
    "parse_decision",
    "process_approval_decision",
]
