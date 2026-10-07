from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from typing import Any, Literal

from openminion.modules.tool.plugin_api import (
    BLOCKCHAIN_CONFIRMATION_PREVIEW_INVALID_MESSAGE,
    POLICY_AUTHORIZATION_DESCRIPTORS,
    BlockchainSendConfirmationPreview,
    ToolConfirmationPreview,
    is_policy_authorization_pair,
)

from ..constants import (
    BLOCKCHAIN_POLICY_TOOL,
    BLOCKCHAIN_SEND_METHOD,
    OPS_COMMAND_CONFIRMATION_TTL_SECONDS,
    OPS_COMMAND_POLICY_TOOL,
    OPS_COMMAND_RUN_METHOD,
    POLICY_CONFIRM_RESPONSE_AFFIRM,
    POLICY_CONFIRM_RESPONSE_DENY,
    POLICY_CONFIRM_RESPONSE_UNCLEAR,
    POLICY_DECISION_ALLOW,
    POLICY_DECISION_DENY,
    POLICY_MODE_ENFORCE,
    POLICY_MODE_ENFORCE_SAFE,
)
from ..models import (
    ContextSummary,
    InvocationSummary,
    PendingPolicyConfirmation,
    PolicyConfig,
    PolicyDecision,
    RiskSpec,
    sanitize_args,
)
from ..storage import PolicyStore

_BLOCKCHAIN_PREVIEW_ERROR_REASONS = {
    "request_schema",
    "preparation_digest",
    "call_context",
    "calldata_limit",
    "preview_limit",
}


def is_exact_blockchain_send(tool: str, method: str) -> bool:
    return tool == BLOCKCHAIN_POLICY_TOOL and method == BLOCKCHAIN_SEND_METHOD


def is_exact_ops_command(tool: str, method: str) -> bool:
    return tool == OPS_COMMAND_POLICY_TOOL and method == OPS_COMMAND_RUN_METHOD


def get_or_create_exact_confirmation(
    *,
    store: PolicyStore,
    invocation: InvocationSummary,
    context: ContextSummary,
    subject_id: str,
    confirmation_preview: ToolConfirmationPreview | None,
) -> PendingPolicyConfirmation:
    descriptor = POLICY_AUTHORIZATION_DESCRIPTORS.get(
        (invocation.tool, invocation.method)
    )
    if is_exact_blockchain_send(invocation.tool, invocation.method):
        assert isinstance(confirmation_preview, BlockchainSendConfirmationPreview)
        preview = asdict(confirmation_preview)
    elif descriptor is not None:
        assert isinstance(confirmation_preview, dict)
        preview = dict(confirmation_preview)
    else:
        preview = {
            "plan_id": str(invocation.args.get("plan_id", "")),
            "plan_hash": str(invocation.args.get("plan_hash", "")),
            "target_id": str(invocation.args.get("target_id", "")),
            "target_revision": invocation.args.get("target_revision"),
            "argv": list(invocation.args.get("argv", ())),
            "cwd": str(invocation.args.get("cwd", "")),
            "timeout_seconds": invocation.args.get("timeout_seconds"),
            "expires_at": str(invocation.args.get("expires_at", "")),
        }
    return store.get_or_create_pending_confirmation(
        subject_id=subject_id,
        tool=invocation.tool,
        method=invocation.method,
        invocation_hash=invocation.invocation_hash,
        invocation_id=invocation.invocation_id,
        trace_id=context.trace_id,
        session_id=context.session_id,
        preview=preview,
        ttl_seconds=descriptor.confirmation_ttl_seconds
        if descriptor is not None
        else OPS_COMMAND_CONFIRMATION_TTL_SECONDS,
    )


def resolve_exact_ops_decision(
    *,
    store: PolicyStore,
    invocation: InvocationSummary,
    context: ContextSummary,
    subject_id: str,
    risk: RiskSpec,
) -> PolicyDecision | None:
    grant = store.resolve_matching_active_grant_for_use(
        subject_id=subject_id,
        tool=invocation.tool,
        method=invocation.method,
        invocation_hash=invocation.invocation_hash,
        session_id=context.session_id,
    )
    if grant is None:
        return None
    return PolicyDecision(
        decision=POLICY_DECISION_ALLOW,
        reason_code="EXACT_PENDING_ALLOW",
        reason="Allowed by exact pending confirmation",
        risk=risk,
        matched_grant_id=grant.grant_id,
        approval_id=grant.approval_id,
        invocation_hash=invocation.invocation_hash,
        details={
            "grant_id": grant.grant_id,
            "duration_type": grant.duration_type,
        },
    )


def blockchain_preview_invalid_decision(
    invocation_hash: str, risk: RiskSpec, reason: str | None
) -> PolicyDecision:
    return PolicyDecision(
        decision=POLICY_DECISION_DENY,
        reason_code="BLOCKCHAIN_CONFIRMATION_PREVIEW_INVALID",
        reason=BLOCKCHAIN_CONFIRMATION_PREVIEW_INVALID_MESSAGE,
        risk=risk,
        invocation_hash=invocation_hash,
        details={
            "reason": reason
            if reason in _BLOCKCHAIN_PREVIEW_ERROR_REASONS
            else "request_schema"
        },
    )


def authorization_preflight_decision(
    *,
    invocation: InvocationSummary,
    context: ContextSummary,
    risk: RiskSpec,
    mode: str,
    confirmation_preview: ToolConfirmationPreview | None,
    confirmation_preview_error: str | None,
) -> PolicyDecision | None:
    if is_exact_blockchain_send(invocation.tool, invocation.method):
        if confirmation_preview is None:
            return blockchain_preview_invalid_decision(
                invocation.invocation_hash,
                risk,
                confirmation_preview_error,
            )
    elif (
        descriptor := POLICY_AUTHORIZATION_DESCRIPTORS.get(
            (invocation.tool, invocation.method)
        )
    ) is not None:
        if (
            descriptor.required_subject_id is not None
            and context.subject_id != descriptor.required_subject_id
        ) or (descriptor.requires_session and not context.session_id):
            return PolicyDecision(
                decision=POLICY_DECISION_DENY,
                reason_code="SUBJECT_UNAVAILABLE",
                reason="Authorization requires the trusted subject and session.",
                risk=risk,
                invocation_hash=invocation.invocation_hash,
            )
        if not isinstance(confirmation_preview, dict):
            return PolicyDecision(
                decision=POLICY_DECISION_DENY,
                reason_code=f"{invocation.tool.upper()}_CONFIRMATION_PREVIEW_INVALID",
                reason="Approval preview could not be verified.",
                risk=risk,
                invocation_hash=invocation.invocation_hash,
            )
    if (
        is_policy_authorization_pair(invocation.tool, invocation.method)
        or is_exact_ops_command(invocation.tool, invocation.method)
    ) and mode not in {POLICY_MODE_ENFORCE, POLICY_MODE_ENFORCE_SAFE}:
        action = (
            "operations command"
            if is_exact_ops_command(invocation.tool, invocation.method)
            else f"{invocation.tool} {invocation.method}"
        )
        return PolicyDecision(
            decision=POLICY_DECISION_DENY,
            reason_code="POLICY_MODE_UNSUPPORTED",
            reason=f"Enforcing policy is required for {action}.",
            risk=risk,
            invocation_hash=invocation.invocation_hash,
            details={"mode": mode},
        )
    return None


def _normalize_confirmation_token(value: str) -> str:
    token = str(value or "").strip().lower().rstrip(".,!?")
    return " ".join(part for part in token.split() if part)


def parse_confirmation_response(
    text: str,
    *,
    affirmative_tokens: Iterable[str] | None = None,
    negative_tokens: Iterable[str] | None = None,
) -> Literal["affirm", "deny", "unclear"]:
    normalized = _normalize_confirmation_token(text)
    if not normalized:
        return POLICY_CONFIRM_RESPONSE_UNCLEAR

    affirmative = {
        _normalize_confirmation_token(token)
        for token in (
            affirmative_tokens
            if affirmative_tokens is not None
            else PolicyConfig().affirmative_tokens
        )
        if _normalize_confirmation_token(token)
    }
    negative = {
        _normalize_confirmation_token(token)
        for token in (
            negative_tokens
            if negative_tokens is not None
            else PolicyConfig().negative_tokens
        )
        if _normalize_confirmation_token(token)
    }

    if normalized in affirmative and normalized not in negative:
        return POLICY_CONFIRM_RESPONSE_AFFIRM
    if normalized in negative and normalized not in affirmative:
        return POLICY_CONFIRM_RESPONSE_DENY
    return POLICY_CONFIRM_RESPONSE_UNCLEAR


def build_confirm_request(
    *,
    invocation: InvocationSummary,
    context: ContextSummary,
    risk: RiskSpec,
    target_scope: dict[str, Any],
) -> dict[str, Any]:
    scope_preview = {
        "allow_once": {
            "tool": invocation.tool,
            "method": invocation.method,
            "invocation_hash": invocation.invocation_hash,
        },
        "allow_until": {
            "tool": invocation.tool,
            "method": invocation.method,
            "target": dict(target_scope),
        },
        "allow_session": {
            "tool": invocation.tool,
            "method": invocation.method,
            "target": dict(target_scope),
            "session_id": context.session_id,
        },
        "allow_forever": {
            "tool": invocation.tool,
            "method": invocation.method,
            "target": dict(target_scope),
        },
    }
    return {
        "trace_id": context.trace_id,
        "invocation_id": invocation.invocation_id,
        "summary": {
            "tool": invocation.tool,
            "method": invocation.method,
            "args": sanitize_args(invocation.args),
        },
        "risk": {
            "risk_class": risk.risk_class,
            "side_effects": risk.side_effects,
            "reversibility": risk.reversibility,
        },
        "suggested_choices": [
            {"action": "allow_once", "label": "Allow once"},
            {
                "action": "allow_until",
                "label": "Allow for 10 minutes",
                "until_seconds": 600,
            },
            {"action": "allow_session", "label": "Allow for this session"},
            {"action": "allow_forever", "label": "Allow forever (scoped)"},
            {"action": "deny", "label": "Deny"},
        ],
        "scope_preview": scope_preview,
        "deny_option": {"action": "deny"},
    }
