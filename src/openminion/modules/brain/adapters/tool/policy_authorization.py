from __future__ import annotations

from typing import Any

from openminion.modules.tool.plugin_api import is_policy_authorization_pair
from openminion.tools.commerce.authorization import (
    consume_commerce_action_authorization,
    consume_commerce_place_authorization,
    consume_commerce_prepare_authorization,
)

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
) -> dict[str, Any]:
    if context.tool_name == "blockchain.send_transaction":
        args, context.policy_authorization = authorize_blockchain_send(
            args,
            context,
            policy_ctl,
        )
    elif context.tool_name == "commerce.prepare_order":
        context.policy_authorization = consume_commerce_prepare_authorization(
            policy_ctl=policy_ctl,
            permission_mode=context.permission_mode,
            args=args,
            subject_id="local",
            session_id=context.session_id,
        )
    elif context.tool_name == "commerce.place_order":
        context.policy_authorization = consume_commerce_place_authorization(
            policy_ctl=policy_ctl,
            permission_mode=context.permission_mode,
            args=args,
            subject_id="local",
            session_id=context.session_id,
        )
    elif context.tool_name == "commerce.apply_order_action":
        context.policy_authorization = consume_commerce_action_authorization(
            policy_ctl=policy_ctl,
            permission_mode=context.permission_mode,
            args=args,
            subject_id="local",
            session_id=context.session_id,
        )
    return args


__all__ = [
    "authorize_exact_tool_call",
    "requires_canonical_policy",
    "requires_policy_confirmation",
]
