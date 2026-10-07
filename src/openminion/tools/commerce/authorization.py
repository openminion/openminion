"""Exact one-time authorization for commerce mutations."""

from __future__ import annotations

from typing import Any, Literal, Mapping, cast

from openminion.modules.policy.models import PolicyControlError
from openminion.modules.tool.contracts.schemas import TOOL_ERROR_CONFIRM_REQUIRED
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.plugin_api import (
    PolicyAuthorization,
    stable_invocation_hash,
)


def canonical_commerce_args(args: dict[str, Any]) -> dict[str, Any]:
    def canonical(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): canonical(item)
                for key, item in value.items()
                if item is not None and not str(key).startswith("_")
            }
        if isinstance(value, (list, tuple)):
            return [canonical(item) for item in value]
        return value

    return cast(dict[str, Any], canonical(args))


def authorize_commerce_call(
    args: dict[str, Any], context: Any, policy_ctl: Any
) -> PolicyAuthorization:
    return consume_commerce_authorization(
        method=context.tool_name.rsplit(".", 1)[1],
        policy_ctl=policy_ctl,
        permission_mode=context.permission_mode,
        args=args,
        subject_id=context.subject_id,
        session_id=context.session_id,
    )


def confirmation_preview(
    args: dict[str, Any],
    *,
    tool_name: str,
    subject_id: str,
    session_id: str,
    tool_resources: Mapping[str, Any],
) -> dict[str, Any]:
    from .confirmation import commerce_confirmation_lines, commerce_confirmation_payload

    preview = tool_resources["commerce"].resolve_confirmation_preview(
        tool_name=tool_name,
        args=args,
        subject_id=subject_id,
        session_id=session_id,
    )
    return {
        **commerce_confirmation_payload(preview),
        "display_lines": commerce_confirmation_lines(preview),
    }


def consume_commerce_authorization(
    *,
    method: Literal["prepare_order", "place_order", "apply_order_action"],
    policy_ctl: Any | None,
    permission_mode: str,
    args: dict[str, Any],
    subject_id: str,
    session_id: str | None,
) -> PolicyAuthorization:
    mutation = {
        "prepare_order": "preparation",
        "place_order": "placement",
        "apply_order_action": "order action",
    }[method]
    policy_mode = str(policy_ctl.mode()) if policy_ctl is not None else ""
    if (
        policy_ctl is None
        or policy_mode not in {"enforce", "enforce_safe"}
        or permission_mode in {"bypass", "auto"}
        or subject_id != "local"
        or not session_id
    ):
        raise ToolRuntimeError(
            "POLICY_DENIED",
            f"Commerce {mutation} requires enforcing policy authorization.",
            {"commerce_code": "POLICY_MODE_UNSUPPORTED"},
        )

    invocation_hash = stable_invocation_hash(
        tool="commerce", method=method, args=canonical_commerce_args(args)
    )
    try:
        grant = policy_ctl.resolve_matching_active_grant_for_use(
            subject_id=subject_id,
            tool="commerce",
            method=method,
            invocation_hash=invocation_hash,
            session_id=session_id,
        )
    except PolicyControlError as exc:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            f"Commerce {mutation} authorization was rejected.",
            {"commerce_code": exc.code},
        ) from exc
    if grant is None:
        raise ToolRuntimeError(
            TOOL_ERROR_CONFIRM_REQUIRED,
            f"Exact one-time commerce {mutation} approval is required.",
            {"commerce_code": TOOL_ERROR_CONFIRM_REQUIRED},
        )
    return PolicyAuthorization(
        tool="commerce",
        method=method,
        invocation_hash=invocation_hash,
        approval_id=str(grant.approval_id),
        grant_id=str(grant.grant_id),
        duration_type="once",
        subject_id=subject_id,
        session_id=session_id,
    )


__all__ = [
    "authorize_commerce_call",
    "canonical_commerce_args",
    "confirmation_preview",
    "consume_commerce_authorization",
]
