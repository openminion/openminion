"""Security policy filtering for agent tool execution."""

from types import SimpleNamespace
from typing import Any

from openminion.modules.tool.base import ToolExecutionResult
from openminion.modules.policy import (
    SecurityPolicyContext,
    ToolBudgetState,
    default_internal_actor,
)
from openminion.modules.policy.adapters.tool import (
    build_execution_boundary_policy_adapter,
)
from openminion.modules.tool.sidecars import (
    approve_sidecar_for_allowed_decision,
    blocked_tool_result,
    denied_tool_event,
    filter_call_without_policy,
    maybe_allow_denied_call_with_operator_approval,
    provider_call_from_decision,
    sidecar_for_tool,
)

from .ports import ProviderToolCall, TurnFlowServicePort


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
) -> tuple[
    list[ProviderToolCall],
    list[dict[str, str]],
    list[ToolExecutionResult],
    dict[str, str],
]:
    if service_port.tools is None:
        return list(tool_calls or []), [], [], {}

    security_events: list[dict[str, str]] = []
    denied_results: list[ToolExecutionResult] = []
    allowed_calls: list[ProviderToolCall] = []
    runtime_env_overrides: dict[str, str] = {}
    approval_callback = getattr(runtime, "approval_callback", None)
    for call in tool_calls:
        tool_name = str(getattr(call, "name", "") or "").strip()
        tool_args = dict(getattr(call, "arguments", {}) or {})
        sidecar = sidecar_for_tool(service_port.tools, tool_name)
        if policy_adapter is None:
            allowed, denied = await filter_call_without_policy(
                call=call,
                tool_name=tool_name,
                sidecar=sidecar,
                approval_callback=approval_callback,
                runtime_env_overrides=runtime_env_overrides,
            )
            if denied is not None:
                denied_results.append(denied)
                break
            if allowed is not None:
                allowed_calls.append(allowed)
            continue
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
        approved_call = await maybe_allow_denied_call_with_operator_approval(
            call=call,
            tool_name=tool_name,
            tool_args=tool_args,
            decision=decision,
            approval_callback=approval_callback,
        )
        if approved_call is not None:
            allowed_calls.append(approved_call)
            continue
        if decision.allowed and sidecar and approval_callback is not None:
            event, denied = await approve_sidecar_for_allowed_decision(
                call=call,
                tool_name=tool_name,
                sidecar=sidecar,
                approval_callback=approval_callback,
                runtime_env_overrides=runtime_env_overrides,
            )
            if event is not None:
                security_events.append(event)
            if denied is not None:
                denied_results.append(denied)
                break
        if decision.allowed:
            allowed_calls.append(
                provider_call_from_decision(
                    call=call,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    decision=decision,
                )
            )
            continue
        event, source = denied_tool_event(
            call=call, tool_name=tool_name, decision=decision
        )
        security_events.append(event)
        denied_results.append(
            blocked_tool_result(
                call=call,
                tool_name=tool_name,
                decision=decision,
                event_kind=event["event_kind"],
                denial_source=source,
            )
        )
        break
    return allowed_calls, security_events, denied_results, runtime_env_overrides


__all__ = ["build_policy_adapter", "filter_allowed_tool_calls"]
