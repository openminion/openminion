"""Security policy filtering for agent tool execution."""

from types import SimpleNamespace
from typing import Any

from openminion.modules.telemetry.trace.phase_timing import active_chat_phase
from openminion.modules.tool.base import ToolExecutionResult
from openminion.services.security.policy import (
    DECISION_REQUIRE_APPROVAL,
    SecurityPolicyContext,
    ToolBudgetState,
    default_internal_actor,
)
from openminion.services.security.tool_execution import (
    build_execution_boundary_policy_adapter,
)

from .ports import ProviderToolCall, TurnFlowServicePort


def _blocked_tool_result(
    *,
    call: ProviderToolCall,
    tool_name: str,
    decision: Any,
    event_kind: str,
    denial_source: str,
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


def build_policy_adapter(
    service_port: TurnFlowServicePort,
    runtime: Any,
    *,
    tool_budget_state: ToolBudgetState | None,
    turn_boundary_adapter: Any,
) -> Any | None:
    if service_port.security_policy is None or service_port.tools is None:
        return None

    def policy_lookup(tool_name: str) -> Any:
        profile = service_port.tools.policy_for(tool_name)
        return SimpleNamespace(
            required_scopes_all=(),
            risk=getattr(profile, "risk", "medium"),
            budget_cost=getattr(profile, "budget_cost", 1),
        )

    inbound = runtime.inbound
    return build_execution_boundary_policy_adapter(
        policy=service_port.security_policy,
        actor=default_internal_actor(service_port.identity_agent_id),
        context=SecurityPolicyContext(
            channel=inbound.channel,
            target=inbound.target,
            session_id=inbound.metadata.get("session_id", ""),
            run_id=inbound.metadata.get("run_id", ""),
        ),
        tool_policy_lookup=policy_lookup,
        budget_state=tool_budget_state,
        blast_radius_adapter=turn_boundary_adapter,
    )


async def filter_allowed_tool_calls(
    service_port: TurnFlowServicePort,
    runtime: Any,
    tool_calls: list[ProviderToolCall],
    *,
    policy_adapter: Any | None,
) -> tuple[list[ProviderToolCall], list[dict[str, str]], list[ToolExecutionResult]]:
    if policy_adapter is None or service_port.tools is None:
        return list(tool_calls or []), [], []

    security_events: list[dict[str, str]] = []
    denied_results: list[ToolExecutionResult] = []
    allowed_calls: list[ProviderToolCall] = []
    approval_callback = getattr(runtime, "approval_callback", None)
    for call in tool_calls:
        tool_name = str(getattr(call, "name", "") or "").strip()
        tool_args = dict(getattr(call, "arguments", {}) or {})
        profile = service_port.tools.policy_for(tool_name)
        decision = policy_adapter.evaluate(
            tool_name=tool_name,
            tool_spec=SimpleNamespace(
                name=tool_name,
                dangerous=str(profile.risk or "").strip().lower()
                in {"high", "critical"},
            ),
            args=tool_args,
        )
        if (
            not decision.allowed
            and approval_callback is not None
            and (
                decision.requires_confirm or decision.code == DECISION_REQUIRE_APPROVAL
            )
        ):
            with active_chat_phase("approval_wait"):
                approved = bool(
                    await approval_callback(
                        tool_name,
                        tool_args,
                        str(getattr(call, "id", "") or ""),
                    )
                )
            if approved:
                allowed_calls.append(
                    ProviderToolCall(
                        name=tool_name,
                        arguments=decision.modified_args or tool_args,
                        id=str(getattr(call, "id", "") or ""),
                        source=str(getattr(call, "source", "") or ""),
                    )
                )
                continue
        if decision.allowed:
            allowed_calls.append(
                ProviderToolCall(
                    name=tool_name,
                    arguments=decision.modified_args or tool_args,
                    id=str(getattr(call, "id", "") or ""),
                    source=str(getattr(call, "source", "") or ""),
                )
            )
            continue
        event_kind = (
            "approval_required"
            if decision.requires_confirm or decision.code == DECISION_REQUIRE_APPROVAL
            else "policy_denied"
        )
        reason_code = str(decision.reason or "policy_denied")
        source = "budget" if reason_code.startswith("tool_budget") else "policy"
        details = dict(decision.details or {})
        security_events.append(
            {
                "event_kind": event_kind,
                "reason_code": reason_code,
                "policy_version": str(details.get("policy_version", "") or "v1"),
                "decision": str(details.get("decision", "") or decision.code),
                "tool_name": tool_name,
                "call_id": str(getattr(call, "id", "") or ""),
                "source": source,
            }
        )
        denied_results.append(
            _blocked_tool_result(
                call=call,
                tool_name=tool_name,
                decision=decision,
                event_kind=event_kind,
                denial_source=source,
            )
        )
        break
    return allowed_calls, security_events, denied_results


__all__ = ["build_policy_adapter", "filter_allowed_tool_calls"]
