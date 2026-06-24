from dataclasses import dataclass, field
from typing import Any

from ..constants import TOOL_AUTHORING_REGISTER_REASON


@dataclass(frozen=True)
class _PolicyGrantPayload:
    effect: str
    subject_id: str
    tool: str
    method: str = "*"
    target_json: dict[str, Any] = field(default_factory=dict)
    duration_type: str = "forever"
    expires_at: str | None = None
    session_id: str | None = None
    invocation_hash: str | None = None
    max_uses: int | None = None
    reason: str | None = None
    created_trace_id: str | None = None
    risk_floor: str | None = None


def issue_power_user_grant(
    *,
    policy_ctl: Any,
    tool_name: str,
    subject_id: str,
) -> str:
    return str(
        policy_ctl.create_grant(
            _PolicyGrantPayload(
                effect="allow",
                subject_id=subject_id,
                tool=tool_name,
                method="*",
                duration_type="forever",
                reason=TOOL_AUTHORING_REGISTER_REASON,
            )
        )
    )


def revoke_grant(*, policy_ctl: Any, grant_id: str) -> bool:
    return bool(policy_ctl.revoke_grant(grant_id))


__all__ = ["issue_power_user_grant", "revoke_grant"]
