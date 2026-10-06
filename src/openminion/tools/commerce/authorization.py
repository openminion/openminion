"""Exact one-time authorization for commerce mutations."""

from __future__ import annotations

from typing import Any

from openminion.modules.policy.models import PolicyControlError
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.plugin_api import PolicyAuthorization, stable_invocation_hash


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

    return canonical(args)


def consume_commerce_prepare_authorization(
    *,
    policy_ctl: Any | None,
    permission_mode: str,
    args: dict[str, Any],
    subject_id: str,
    session_id: str | None,
) -> PolicyAuthorization:
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
            "Commerce preparation requires enforcing policy authorization.",
            {"commerce_code": "POLICY_MODE_UNSUPPORTED"},
        )

    invocation_hash = stable_invocation_hash(
        tool="commerce", method="prepare_order", args=canonical_commerce_args(args)
    )
    try:
        grant = policy_ctl.resolve_matching_active_grant_for_use(
            subject_id=subject_id,
            tool="commerce",
            method="prepare_order",
            invocation_hash=invocation_hash,
            session_id=session_id,
        )
    except PolicyControlError as exc:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "Commerce preparation authorization was rejected.",
            {"commerce_code": exc.code},
        ) from exc
    if grant is None:
        raise ToolRuntimeError(
            "CONFIRM_REQUIRED",
            "Exact one-time commerce preparation approval is required.",
            {"commerce_code": "CONFIRM_REQUIRED"},
        )
    return PolicyAuthorization(
        tool="commerce",
        method="prepare_order",
        invocation_hash=invocation_hash,
        approval_id=str(grant.approval_id),
        grant_id=str(grant.grant_id),
        duration_type="once",
        subject_id=subject_id,
        session_id=session_id,
    )


__all__ = ["canonical_commerce_args", "consume_commerce_prepare_authorization"]
