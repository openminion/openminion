"""Exact one-time authorization for commerce mutations."""

from __future__ import annotations

from typing import Any, Literal, cast

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


def _consume_commerce_authorization(
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


def consume_commerce_prepare_authorization(
    *,
    policy_ctl: Any | None,
    permission_mode: str,
    args: dict[str, Any],
    subject_id: str,
    session_id: str | None,
) -> PolicyAuthorization:
    return _consume_commerce_authorization(
        method="prepare_order",
        policy_ctl=policy_ctl,
        permission_mode=permission_mode,
        args=args,
        subject_id=subject_id,
        session_id=session_id,
    )


def consume_commerce_place_authorization(
    *,
    policy_ctl: Any | None,
    permission_mode: str,
    args: dict[str, Any],
    subject_id: str,
    session_id: str | None,
) -> PolicyAuthorization:
    return _consume_commerce_authorization(
        method="place_order",
        policy_ctl=policy_ctl,
        permission_mode=permission_mode,
        args=args,
        subject_id=subject_id,
        session_id=session_id,
    )


def consume_commerce_action_authorization(
    *,
    policy_ctl: Any | None,
    permission_mode: str,
    args: dict[str, Any],
    subject_id: str,
    session_id: str | None,
) -> PolicyAuthorization:
    return _consume_commerce_authorization(
        method="apply_order_action",
        policy_ctl=policy_ctl,
        permission_mode=permission_mode,
        args=args,
        subject_id=subject_id,
        session_id=session_id,
    )


__all__ = [
    "canonical_commerce_args",
    "consume_commerce_action_authorization",
    "consume_commerce_place_authorization",
    "consume_commerce_prepare_authorization",
]
