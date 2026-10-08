from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import cast

from openminion.modules.tool.plugin_api import is_policy_authorization_pair

from .constants import (
    OPS_COMMAND_POLICY_TOOL,
    OPS_COMMAND_RUN_METHOD,
    POLICY_DECISION_ALLOW,
    POLICY_DECISION_DENY,
    POLICY_DURATION_FOREVER,
    POLICY_DURATION_ONCE,
    POLICY_DURATION_SESSION,
    POLICY_DURATION_TYPES,
    POLICY_DURATION_UNTIL,
    POLICY_GRANT_EFFECT_DENY,
    POLICY_GRANT_EFFECTS,
)
from .models import (
    DurationType,
    InvocationSummary,
    PolicyDecision,
    PolicyGrant,
    PolicyGrantInput,
    RiskClass,
    RiskSpec,
)
from .storage import PolicyStore


@dataclass(frozen=True)
class GrantMatch:
    grant: PolicyGrant
    score: int


def validate_grant_input(grant: PolicyGrantInput) -> None:
    if grant.effect not in POLICY_GRANT_EFFECTS:
        raise ValueError("grant.effect must be allow|deny")
    if grant.duration_type not in POLICY_DURATION_TYPES:
        raise ValueError("grant.duration_type must be once|until|session|forever")
    if grant.duration_type == POLICY_DURATION_SESSION and not grant.session_id:
        raise ValueError("session grants require a non-empty session_id")
    if (
        requires_once_duration(
            tool=grant.tool,
            method=grant.method,
            risk_floor=grant.risk_floor,
        )
        and grant.duration_type != POLICY_DURATION_ONCE
    ):
        raise ValueError(f"{grant.tool}.{grant.method} grants must be allow once")
    if grant.duration_type == POLICY_DURATION_ONCE and not grant.invocation_hash:
        raise ValueError("once grants require invocation_hash")
    if grant.duration_type == POLICY_DURATION_UNTIL:
        if not grant.expires_at:
            raise ValueError("until grants require expires_at")
        try:
            expires_at = datetime.fromisoformat(grant.expires_at)
        except ValueError as exc:
            raise ValueError("expires_at must be an ISO-8601 timestamp") from exc
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        grant.expires_at = expires_at.astimezone(timezone.utc).isoformat()


def confirmation_grant_terms(
    *,
    action: str,
    session_id: str | None,
    invocation_hash: str,
    until_seconds: int | None,
) -> tuple[DurationType, str | None, str | None, str | None]:
    if action == "allow_once":
        return cast(DurationType, POLICY_DURATION_ONCE), None, None, invocation_hash
    if action == "allow_until":
        seconds = max(1, int(until_seconds or 600))
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=seconds)
        ).isoformat()
        return cast(DurationType, POLICY_DURATION_UNTIL), expires_at, None, None
    if action in {"allow_session", "allow_session_exact"}:
        if not session_id:
            raise ValueError("session approval requires a non-empty session_id")
        exact_hash = invocation_hash if action == "allow_session_exact" else None
        return cast(DurationType, POLICY_DURATION_SESSION), None, session_id, exact_hash
    if action == "allow_forever":
        return cast(DurationType, POLICY_DURATION_FOREVER), None, None, None
    raise ValueError(f"Unsupported confirmation action: {action}")


def requires_once_duration(
    *,
    tool: str,
    method: str,
    risk_floor: RiskClass | None = None,
) -> bool:
    normalized_tool = str(tool or "").strip().lower()
    normalized_method = str(method or "").strip().lower()
    return (
        normalized_tool == "financial"
        or is_policy_authorization_pair(normalized_tool, normalized_method)
        or (normalized_tool == "ops.command" and normalized_method == "run")
        or (normalized_tool == "project" and normalized_method == "start")
        or risk_floor == "financial"
    )


def grant_specificity_score(grant: PolicyGrant) -> int:
    score = 8 if grant.tool != "*" else 0
    score += 6 if grant.method != "*" else 0
    if grant.target_json:
        score += 4 + len(grant.target_json)
    return score + bool(grant.risk_floor)


def select_grant_match(matches: list[GrantMatch]) -> GrantMatch | None:
    if not matches:
        return None
    best_score = max(item.score for item in matches)
    same = [item for item in matches if item.score == best_score]
    deny = [item for item in same if item.grant.effect == POLICY_GRANT_EFFECT_DENY]
    return max(deny or same, key=lambda item: item.grant.created_at)


def matching_grant_decision(
    *,
    store: PolicyStore,
    matches: list[GrantMatch],
    invocation: InvocationSummary,
    risk: RiskSpec,
    consume_grants: bool,
) -> PolicyDecision | None:
    if requires_once_duration(
        tool=invocation.tool,
        method=invocation.method,
        risk_floor=risk.risk_class,
    ):
        matches = [
            match
            for match in matches
            if match.grant.effect == POLICY_GRANT_EFFECT_DENY
            or match.grant.duration_type == POLICY_DURATION_ONCE
        ]
    if is_policy_authorization_pair(invocation.tool, invocation.method):
        matches = [match for match in matches if match.grant.approval_id]
    selected = select_grant_match(matches)
    if selected is None:
        return None
    grant = selected.grant
    if grant.effect == POLICY_GRANT_EFFECT_DENY:
        return PolicyDecision(
            decision=POLICY_DECISION_DENY,
            reason_code="EXPLICIT_DENY",
            reason="Denied by explicit grant rule",
            risk=risk,
            matched_grant_id=grant.grant_id,
            details={"grant_id": grant.grant_id},
        )
    exact_ops = (
        invocation.tool == OPS_COMMAND_POLICY_TOOL
        and invocation.method == OPS_COMMAND_RUN_METHOD
    )
    if exact_ops:
        return None
    if consume_grants and not is_policy_authorization_pair(
        invocation.tool, invocation.method
    ):
        if store.consume_grant_use(grant.grant_id) is None:
            return None
    return PolicyDecision(
        decision=POLICY_DECISION_ALLOW,
        reason_code="EXPLICIT_ALLOW",
        reason="Allowed by explicit grant",
        risk=risk,
        matched_grant_id=grant.grant_id,
        approval_id=grant.approval_id,
        invocation_hash=invocation.invocation_hash,
        details={"grant_id": grant.grant_id},
    )


__all__ = (
    "confirmation_grant_terms",
    "GrantMatch",
    "grant_specificity_score",
    "matching_grant_decision",
    "requires_once_duration",
    "select_grant_match",
    "validate_grant_input",
)
