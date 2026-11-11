from __future__ import annotations

import asyncio
import concurrent.futures
import time
from collections.abc import Mapping
from typing import Any

from openminion.modules.brain.constants import BRAIN_STATE_ERROR
from openminion.modules.policy.models import PolicyControlError
from openminion.modules.runtime.sync import raise_if_cancelled
from openminion.modules.telemetry.trace.phase_timing import active_chat_phase
from openminion.modules.tool.plugin_api import (
    PolicyAuthorization,
    is_policy_authorization_pair,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolSpec

from .blockchain_authorization import authorize_blockchain_send
from .policy_context import _approval_policy_facts
from .results import _error_envelope, _policy_error


def requires_canonical_policy(tool_name: str) -> bool:
    tool, method = (
        tool_name.rsplit(".", 1) if "." in tool_name else (tool_name, "default")
    )
    return tool_name == "ops.command.run" or is_policy_authorization_pair(tool, method)


def requires_policy_confirmation(decision: Any) -> bool:
    return bool(decision.requires_confirm) or str(decision.code or "").lower() in {
        "require_approval",
        "confirm_required",
    }


def authorize_exact_tool_call(
    args: dict[str, Any],
    context: Any,
    policy_ctl: Any | None,
    *,
    spec: ToolSpec,
) -> dict[str, Any]:
    if spec.canonical_args is not None:
        try:
            args = spec.canonical_args(args)
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            raise ToolRuntimeError(
                "POLICY_DENIED", "Authorization arguments could not be verified."
            ) from exc
    if context.tool_name == "blockchain.send_transaction":
        args, context.policy_authorization = authorize_blockchain_send(
            args,
            context,
            policy_ctl,
        )
    else:
        tool, method = (
            context.tool_name.rsplit(".", 1)
            if "." in context.tool_name
            else (context.tool_name, "default")
        )
        if is_policy_authorization_pair(tool, method):
            if spec.policy_authorizer is None:
                raise ToolRuntimeError(
                    "POLICY_DENIED", "Exact action authorizer is unavailable."
                )
            try:
                authorization = spec.policy_authorizer(args, context, policy_ctl)
            except ToolRuntimeError:
                raise
            except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                raise ToolRuntimeError(
                    "POLICY_DENIED", "Exact action authorization failed."
                ) from exc
            if not isinstance(authorization, PolicyAuthorization):
                raise ToolRuntimeError(
                    "POLICY_DENIED", "Exact action authorization is invalid."
                )
            context.policy_authorization = authorization
    return args


def deny_inline_canonical_confirmation(
    policy_ctl: Any, tool_name: str, approval_id: str
) -> None:
    if policy_ctl is None or not requires_canonical_policy(tool_name):
        return
    try:
        policy_ctl.resolve_confirmation(approval_id, "deny")
    except PolicyControlError:
        pass


def invoke_inline_approval_callback(
    callback: Any,
    *,
    policy_ctl: Any,
    tool_name: str,
    args: dict[str, Any],
    approval_id: str,
    details: Mapping[str, Any] | None = None,
    reason_code: str = "",
) -> bool:
    try:
        raise_if_cancelled()
        with active_chat_phase("approval_wait"):
            approved = bool(
                callback(
                    tool_name,
                    dict(args),
                    approval_id,
                    _approval_policy_facts(
                        tool_name=tool_name,
                        details=details,
                        reason_code=reason_code,
                    ),
                )
            )
        raise_if_cancelled()
        return approved
    except (asyncio.CancelledError, concurrent.futures.CancelledError) as exc:
        deny_inline_canonical_confirmation(policy_ctl, tool_name, approval_id)
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise asyncio.CancelledError("Turn cancelled.") from exc


def settle_inline_canonical_confirmation(
    policy_ctl: Any,
    *,
    tool_name: str,
    approval_id: str,
    approved: bool,
    start_time: float,
) -> dict[str, Any] | None:
    if not requires_canonical_policy(tool_name):
        return None
    if policy_ctl is None:
        return _error_envelope(
            status=BRAIN_STATE_ERROR,
            summary="Operations approval is unavailable",
            code="POLICY_DENIED",
            message="Operations approval is unavailable.",
            latency_ms=int((time.monotonic() - start_time) * 1000),
            details={"reason": "action_policy_unavailable"},
        )
    try:
        policy_ctl.resolve_confirmation(
            approval_id,
            "allow_once" if approved else "deny",
        )
    except PolicyControlError as exc:
        return _policy_error(exc, int((time.monotonic() - start_time) * 1000))
    return None


__all__ = [
    "authorize_exact_tool_call",
    "deny_inline_canonical_confirmation",
    "invoke_inline_approval_callback",
    "requires_canonical_policy",
    "requires_policy_confirmation",
    "settle_inline_canonical_confirmation",
]
