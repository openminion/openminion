from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from ..constants import (
    POLICY_CONFIRM_RESPONSE_AFFIRM,
    POLICY_CONFIRM_RESPONSE_DENY,
    POLICY_CONFIRM_RESPONSE_UNCLEAR,
)
from ..models import (
    ContextSummary,
    InvocationSummary,
    PolicyConfig,
    RiskSpec,
    sanitize_args,
)


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
