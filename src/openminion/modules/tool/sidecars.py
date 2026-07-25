"""Tool-runtime sidecar approval helpers."""

from typing import Any

from openminion.modules.telemetry.trace.phase_timing import active_chat_phase
from openminion.modules.tool.base import ToolExecutionResult

SIDECAR_AUTOSTART_ENV_KEYS: dict[str, str] = {"pinchtab": "PINCHTAB_AUTOSTART"}


def sidecar_autostart_env_key(name: str) -> str:
    return SIDECAR_AUTOSTART_ENV_KEYS.get(str(name or "").strip().lower(), "")


def sidecar_for_tool(tools: Any, tool_name: str) -> str:
    sidecar_for = getattr(tools, "sidecar_for", None)
    return str(sidecar_for(tool_name) if callable(sidecar_for) else "").strip()


def sidecar_blocked_result(
    *, call: Any, tool_name: str, sidecar: str
) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name or "unknown",
        ok=False,
        verified=False,
        content="",
        error="sidecar_autostart_denied",
        data={
            "status": "blocked",
            "error_code": "SIDECAR_AUTOSTART_DENIED",
            "reason_code": "sidecar_autostart_denied",
            "denial_source": "policy",
            "blocked_kind": "approval_required",
            "tool_name": tool_name,
            "sidecar": sidecar,
            "call_id": str(getattr(call, "id", "") or ""),
        },
        call_id=str(getattr(call, "id", "") or ""),
        source="policy",
    )


async def approve_sidecar_autostart(
    *, approval_callback: Any, sidecar: str, call: Any
) -> bool:
    return bool(
        await approval_callback(
            f"sidecar.{sidecar}.autostart",
            {"sidecar": sidecar},
            f"{str(getattr(call, 'id', '') or '')}:sidecar:{sidecar}",
        )
    )


async def filter_call_without_policy(
    *,
    call: Any,
    tool_name: str,
    sidecar: str,
    approval_callback: Any | None,
    runtime_env_overrides: dict[str, str],
) -> tuple[Any | None, ToolExecutionResult | None]:
    if not sidecar or approval_callback is None:
        return call, None
    approved = await approve_sidecar_autostart(
        approval_callback=approval_callback, sidecar=sidecar, call=call
    )
    if approved:
        env_key = sidecar_autostart_env_key(sidecar)
        if env_key:
            runtime_env_overrides[env_key] = "1"
        return call, None
    return None, sidecar_blocked_result(call=call, tool_name=tool_name, sidecar=sidecar)


def provider_call_from_decision(
    *, call: Any, tool_name: str, tool_args: dict[str, Any], decision: Any
) -> Any:
    return type(call)(
        name=tool_name,
        arguments=decision.modified_args or tool_args,
        id=str(getattr(call, "id", "") or ""),
        source=str(getattr(call, "source", "") or ""),
    )


async def maybe_allow_denied_call_with_operator_approval(
    *,
    call: Any,
    tool_name: str,
    tool_args: dict[str, Any],
    decision: Any,
    approval_callback: Any | None,
) -> Any | None:
    if approval_callback is None or decision.allowed:
        return None
    if not (decision.requires_confirm or decision.code == "REQUIRE_APPROVAL"):
        return None
    with active_chat_phase("approval_wait"):
        approved = bool(
            await approval_callback(
                tool_name, tool_args, str(getattr(call, "id", "") or "")
            )
        )
    if not approved:
        return None
    return provider_call_from_decision(
        call=call, tool_name=tool_name, tool_args=tool_args, decision=decision
    )


def sidecar_denial_event(*, call: Any, tool_name: str, sidecar: str) -> dict[str, str]:
    return {
        "event_kind": "approval_required",
        "reason_code": "sidecar_autostart_denied",
        "policy_version": "v1",
        "decision": "operator_denied",
        "tool_name": tool_name,
        "call_id": str(getattr(call, "id", "") or ""),
        "source": "policy",
        "sidecar": sidecar,
    }


async def approve_sidecar_for_allowed_decision(
    *,
    call: Any,
    tool_name: str,
    sidecar: str,
    approval_callback: Any | None,
    runtime_env_overrides: dict[str, str],
) -> tuple[dict[str, str] | None, ToolExecutionResult | None]:
    if not sidecar or approval_callback is None:
        return None, None
    env_key = sidecar_autostart_env_key(sidecar)
    if not env_key:
        return None, None
    with active_chat_phase("approval_wait"):
        approved = await approve_sidecar_autostart(
            approval_callback=approval_callback, sidecar=sidecar, call=call
        )
    if approved:
        runtime_env_overrides[env_key] = "1"
        return None, None
    return sidecar_denial_event(
        call=call, tool_name=tool_name, sidecar=sidecar
    ), sidecar_blocked_result(call=call, tool_name=tool_name, sidecar=sidecar)


def blocked_tool_result(
    *, call: Any, tool_name: str, decision: Any, event_kind: str, denial_source: str
) -> ToolExecutionResult:
    reason_code = str(getattr(decision, "reason", "") or "").strip() or "policy_denied"
    decision_code = str(getattr(decision, "code", "") or "").strip() or reason_code
    details = dict(getattr(decision, "details", {}) or {})
    return ToolExecutionResult(
        tool_name=tool_name or "unknown",
        ok=False,
        verified=False,
        content="",
        error=reason_code if denial_source == "budget" else "security_deny",
        data={
            "status": "blocked",
            "error_code": decision_code,
            "reason_code": reason_code,
            "denial_source": denial_source,
            "blocked_kind": event_kind,
            "error_details": details,
            "tool_name": tool_name,
            "call_id": str(getattr(call, "id", "") or ""),
        },
        call_id=str(getattr(call, "id", "") or ""),
        source="policy",
    )


def denied_tool_event(
    *, call: Any, tool_name: str, decision: Any
) -> tuple[dict[str, str], str]:
    event_kind = (
        "approval_required"
        if decision.requires_confirm or decision.code == "REQUIRE_APPROVAL"
        else "policy_denied"
    )
    reason_code = str(decision.reason or "policy_denied")
    source = "budget" if reason_code.startswith("tool_budget") else "policy"
    details = dict(decision.details or {})
    return {
        "event_kind": event_kind,
        "reason_code": reason_code,
        "policy_version": str(details.get("policy_version", "") or "v1"),
        "decision": str(details.get("decision", "") or decision.code),
        "tool_name": tool_name,
        "call_id": str(getattr(call, "id", "") or ""),
        "source": source,
    }, source


__all__ = [
    "SIDECAR_AUTOSTART_ENV_KEYS",
    "approve_sidecar_for_allowed_decision",
    "blocked_tool_result",
    "denied_tool_event",
    "filter_call_without_policy",
    "maybe_allow_denied_call_with_operator_approval",
    "provider_call_from_decision",
    "sidecar_autostart_env_key",
    "sidecar_for_tool",
]
