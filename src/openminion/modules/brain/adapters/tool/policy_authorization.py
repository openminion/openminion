from __future__ import annotations

from typing import Any

from openminion.modules.tool.plugin_api import (
    PolicyAuthorization,
    is_policy_authorization_pair,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolSpec

from .blockchain_authorization import authorize_blockchain_send


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


__all__ = [
    "authorize_exact_tool_call",
    "requires_canonical_policy",
    "requires_policy_confirmation",
]
