from __future__ import annotations

from typing import Any

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.plugin_api import (
    PolicyAuthorization,
    stable_invocation_hash,
)
from openminion.modules.policy.models import PolicyControlError
from openminion.tools.blockchain.confirmation import canonical_blockchain_send_args
from openminion.tools.blockchain.preparations import (
    PreparationReferenceError,
    resolve_prepared_transaction,
)


def resolve_blockchain_send_args(
    args: dict[str, Any],
    session_id: str,
    env: dict[str, str],
) -> dict[str, Any]:
    try:
        return resolve_prepared_transaction(args, session_id=session_id, env=env)
    except PreparationReferenceError as exc:
        raise ToolRuntimeError("PREPARATION_NOT_FOUND", str(exc)) from exc


def consume_blockchain_send_authorization(
    *,
    policy_ctl: Any | None,
    permission_mode: str,
    args: dict[str, Any],
) -> PolicyAuthorization:
    policy_mode = str(policy_ctl.mode()) if policy_ctl is not None else ""
    if (
        policy_ctl is None
        or policy_mode not in {"enforce", "enforce_safe"}
        or permission_mode in {"bypass", "auto"}
    ):
        raise ToolRuntimeError(
            "POLICY_MODE_UNSUPPORTED",
            "Blockchain transaction send requires the canonical policy service.",
        )

    invocation_hash = stable_invocation_hash(
        tool="blockchain",
        method="send_transaction",
        args=canonical_blockchain_send_args(args),
    )
    try:
        grant = policy_ctl.resolve_matching_active_grant_for_use(
            subject_id="local",
            tool="blockchain",
            method="send_transaction",
            invocation_hash=invocation_hash,
        )
    except PolicyControlError as exc:
        if exc.code != "BLOCKCHAIN_CONFIRMATION_PREVIEW_INVALID":
            raise
        raise ToolRuntimeError(
            "BLOCKCHAIN_CONFIRMATION_PREVIEW_INVALID",
            "Blockchain transaction approval preview could not be verified.",
            {
                "stage": "authorization",
                "approval_id": str(exc.details.get("approval_id", "")),
                "broadcast_attempted": False,
            },
        ) from exc
    if grant is None:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "No matching one-time blockchain approval is active.",
        )
    return PolicyAuthorization(
        tool="blockchain",
        method="send_transaction",
        invocation_hash=invocation_hash,
        approval_id=str(grant.approval_id),
        grant_id=str(grant.grant_id),
        duration_type="once",
    )
