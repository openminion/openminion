from __future__ import annotations

import json
from typing import Any, List, Optional

from openminion.base.types import AgentResponse, Message
from openminion.modules.llm.providers.envelope_v2 import CONTRACT_VERSION_V2
from openminion.modules.llm.providers.base import ProviderRequest
from openminion.services.security.policy import ToolBudgetState
from openminion.services.agent.hooks import HookContext

from ..context import build_context
from ..prompt_history import _provider_tool_call_strategy
from .composition import build_service_port, build_turn_executor
from .dependencies import ExecutorDeps
from .response import finalize_turn_response, tool_calls_payload
from .state import TurnRuntimeContext
from .tool_plan import build_tool_plan
from .validators import (
    canonical_tool_chain,
    canonical_tool_name,
    collect_missing_required_args,
    extract_missing_argument_fields,
    is_tool_argument_error,
    looks_like_tool_call_envelope,
)


def _build_executor_deps(service, finalize_response) -> ExecutorDeps:
    return ExecutorDeps(
        finalize_response=finalize_response,
        tool_calls_payload=tool_calls_payload,
        looks_like_tool_call_envelope=looks_like_tool_call_envelope,
        identity_metadata=service._identity_metadata,
        tool_batch_metadata=service._tool_batch_metadata,
        collect_missing_required_args=collect_missing_required_args,
        is_tool_argument_error=is_tool_argument_error,
        extract_missing_argument_fields=extract_missing_argument_fields,
        canonical_tool_name=canonical_tool_name,
    )


def _build_runtime_context(
    service,
    *,
    inbound: Message,
    history: List[Message] | None,
    progress_callback,
    approval_callback,
) -> TurnRuntimeContext:
    plugin_context = HookContext(config=service._config, logger=service._logger)
    applied_inbound = service._plugins.apply_inbound(inbound, plugin_context)
    context_result = build_context(
        service=service,
        inbound=applied_inbound,
        history=history,
    )
    runtime = TurnRuntimeContext(
        inbound=applied_inbound,
        plugin_context=plugin_context,
        system_prompt=context_result.system_prompt,
        provider_history=context_result.provider_history,
        user_message=context_result.user_message,
        untrusted_metadata=context_result.untrusted_metadata,
        untrusted_events=context_result.untrusted_events,
        progress_callback=progress_callback,
        approval_callback=approval_callback,
    )
    return runtime


def _prepare_turn_runtime(
    service,
    *,
    inbound: Message,
    history: List[Message] | None,
    progress_callback,
    approval_callback,
):
    service._last_identity_snippet = None
    service._refresh_identity_runtime_state()
    runtime = _build_runtime_context(
        service,
        inbound=inbound,
        history=history,
        progress_callback=progress_callback,
        approval_callback=approval_callback,
    )
    service_port = build_service_port(service)
    executor = build_turn_executor(service_port=service_port, runtime=runtime)
    return (
        runtime,
        runtime.inbound,
        runtime.plugin_context,
        _provider_tool_call_strategy(service._provider, service._config),
        service_port,
        executor,
    )


def _unavailable_response(
    service,
    *,
    inbound: Message,
    text: str,
    intent_category: str,
    requested_forced_tools: list[str],
    termination_reason: str,
) -> AgentResponse:
    return AgentResponse(
        text=text,
        channel=inbound.channel,
        target=inbound.target,
        metadata={
            "model": "",
            "finish_reason": "tool_calls",
            "intent_category": intent_category or "none",
            "capability_category": intent_category or "none",
            "capability_tool": "",
            "capability_primary": None,
            "capability_final_tool": "",
            "capability_attempted_tools": json.dumps(list(requested_forced_tools)),
            "capability_fallback_chain": json.dumps([]),
            "capability_fallback_trigger_reason": "",
            "tool_loop_termination_reason": termination_reason,
            "tool_contract_version": CONTRACT_VERSION_V2,
            "tool_calls_count": "0",
            "tool_execution_count": "0",
            "fallback_used": "false",
            **service._empty_tool_resolution_metadata(),
            **service._identity_metadata(),
        },
    )


def _prepare_self_improvement_metadata(service, runtime: TurnRuntimeContext) -> None:
    if service._self_improvement and service._self_improvement.enabled:
        runtime.self_improvement_metadata["improvement_application_mode"] = (
            service._self_improvement.application_mode
        )
        runtime.self_improvement_metadata["improvement_notes_applied_count"] = "0"


def _build_and_apply_tool_plan(
    service_port,
    *,
    inbound: Message,
    runtime: TurnRuntimeContext,
    forced_tools: List[str] | None,
    capability_category: str | None,
):
    plan = build_tool_plan(
        service_port,
        inbound=inbound,
        user_message=runtime.user_message,
        forced_tools=forced_tools,
        capability_category=capability_category,
        canonical_tool_name=canonical_tool_name,
        canonical_tool_chain=canonical_tool_chain,
    )
    runtime.user_message = plan.user_message
    return plan


async def _handle_unforced_provider_response(
    service,
    *,
    executor,
    response,
    inbound: Message,
    intent_category: str,
    tool_call_strategy: str,
    tool_budget_state: ToolBudgetState | None,
    deps: ExecutorDeps,
    finalize_response,
):
    if not response.tool_calls:
        return None
    tool_calls_payload_value = tool_calls_payload(response.tool_calls)
    if not service._tools:
        return finalize_response(
            AgentResponse(
                text="Tool call requested but no tool registry is available.",
                channel=inbound.channel,
                target=inbound.target,
                metadata={
                    "model": response.model,
                    "finish_reason": response.finish_reason or "tool_calls",
                    "intent_category": intent_category or "none",
                    "tool_contract_version": CONTRACT_VERSION_V2,
                    "tool_calls_count": str(len(response.tool_calls or [])),
                    "tool_calls": tool_calls_payload_value,
                    **service._identity_metadata(),
                },
            )
        )
    return await executor.handle_unforced_tool_calls(
        initial_response=response,
        intent_category=intent_category,
        tool_call_strategy=tool_call_strategy,
        tool_budget_state=tool_budget_state,
        deps=deps,
    )


async def _run_required_lane(
    *,
    executor,
    intent_category: str,
    effective_forced_tools: list[str],
    fallback_chain: list[str],
    capability_primary: str | None,
    tool_call_strategy: str,
    tool_budget_state: ToolBudgetState | None,
    executor_deps: ExecutorDeps,
):
    return await executor.run_required_tool_lane(
        intent_category=intent_category,
        effective_forced_tools=effective_forced_tools,
        fallback_chain=fallback_chain,
        capability_primary=capability_primary,
        tool_call_strategy=tool_call_strategy,
        tool_budget_state=tool_budget_state,
        deps=executor_deps,
    )


async def _call_initial_provider(*, executor, runtime, tool_call_strategy: str):
    return await executor.call_provider(
        ProviderRequest(
            user_message=runtime.user_message,
            system_prompt=runtime.system_prompt,
            history=runtime.provider_history,
        ),
        tool_call_strategy=tool_call_strategy,
    )


async def _complete_unforced_lane(
    service,
    *,
    executor,
    runtime: TurnRuntimeContext,
    inbound: Message,
    intent_category: str,
    capability_primary: str | None,
    fallback_chain: list[str],
    attempted_tools: list[str],
    capability_fallback_trigger_reason: str | None,
    tool_call_strategy: str,
    tool_budget_state: ToolBudgetState | None,
    executor_deps: ExecutorDeps,
    finalize_response,
) -> AgentResponse:
    response = await _call_initial_provider(
        executor=executor,
        runtime=runtime,
        tool_call_strategy=tool_call_strategy,
    )
    unforced_result = await _handle_unforced_provider_response(
        service,
        executor=executor,
        response=response,
        inbound=inbound,
        intent_category=intent_category,
        tool_call_strategy=tool_call_strategy,
        tool_budget_state=tool_budget_state,
        deps=executor_deps,
        finalize_response=finalize_response,
    )
    if unforced_result is not None:
        return unforced_result
    return service._build_final_stop_response(
        finalize_response=finalize_response,
        response=response,
        inbound=inbound,
        intent_category=intent_category,
        capability_primary=capability_primary,
        fallback_chain=fallback_chain,
        attempted_tools=attempted_tools,
        capability_fallback_trigger_reason=capability_fallback_trigger_reason,
    )


class AgentTurnFlowMixin:
    def _build_executor_deps(
        self,
        *,
        finalize_response: Any,
    ) -> "ExecutorDeps":
        return ExecutorDeps(
            finalize_response=finalize_response,
            tool_calls_payload=tool_calls_payload,
            looks_like_tool_call_envelope=looks_like_tool_call_envelope,
            identity_metadata=self._identity_metadata,
            tool_batch_metadata=self._tool_batch_metadata,
            collect_missing_required_args=collect_missing_required_args,
            is_tool_argument_error=is_tool_argument_error,
            extract_missing_argument_fields=extract_missing_argument_fields,
            canonical_tool_name=canonical_tool_name,
        )

    def _build_unavailable_response(
        self,
        *,
        finalize_response: Any,
        plan: Any,
        intent_category: str | None,
        inbound: Message,
    ) -> AgentResponse | None:
        """Return the early-exit AgentResponse when plan signals tool/capability
        unavailability, or None when the plan is unblocked."""
        if plan.unavailable_reason == "forced_tool_unavailable":
            return finalize_response(
                _unavailable_response(
                    self,
                    inbound=inbound,
                    text="Required tool unavailable",
                    intent_category=intent_category,
                    requested_forced_tools=list(plan.requested_forced_tools),
                    termination_reason="forced_tool_unavailable",
                )
            )
        if plan.unavailable_reason == "capability_tool_unavailable":
            return finalize_response(
                _unavailable_response(
                    self,
                    inbound=inbound,
                    text="Required capability unavailable",
                    intent_category=intent_category,
                    requested_forced_tools=[],
                    termination_reason="capability_tool_unavailable",
                )
            )
        return None

    def _build_no_tool_registry_response(
        self,
        *,
        finalize_response: Any,
        response: Any,
        inbound: Message,
        intent_category: str | None,
        tool_contract_metadata: dict[str, str],
        tool_calls_payload_value: str,
    ) -> AgentResponse:
        return finalize_response(
            AgentResponse(
                text="Tool call requested but no tool registry is available.",
                channel=inbound.channel,
                target=inbound.target,
                metadata={
                    "model": response.model,
                    "finish_reason": response.finish_reason or "tool_calls",
                    "intent_category": intent_category or "none",
                    **tool_contract_metadata,
                    "tool_calls_count": str(len(response.tool_calls or [])),
                    "tool_calls": tool_calls_payload_value,
                    **self._identity_metadata(),
                },
            )
        )

    def _build_final_stop_response(
        self,
        *,
        finalize_response: Any,
        response: Any,
        inbound: Message,
        intent_category: str | None,
        capability_primary: str | None,
        fallback_chain: list[Any],
        attempted_tools: list[str],
        capability_fallback_trigger_reason: str | None,
    ) -> AgentResponse:
        return finalize_response(
            AgentResponse(
                text=response.text,
                channel=inbound.channel,
                target=inbound.target,
                metadata={
                    "model": response.model,
                    "finish_reason": response.finish_reason or "stop",
                    "intent_category": intent_category or "none",
                    "capability_category": intent_category or "none",
                    "capability_primary": capability_primary,
                    "capability_fallback_chain": json.dumps(fallback_chain),
                    "capability_attempted_tools": json.dumps(attempted_tools),
                    "capability_fallback_trigger_reason": capability_fallback_trigger_reason
                    or "",
                    "capability_final_tool": attempted_tools[-1]
                    if attempted_tools
                    else capability_primary or "",
                    **self._identity_metadata(),
                },
            )
        )

    async def run_turn(
        self,
        inbound: Message,
        history: List[Message] = None,
        forced_tools: List[str] = None,
        capability_category: Optional[str] = None,
        progress_callback=None,
        approval_callback=None,
    ) -> AgentResponse:
        """Run a single interaction turn, including tool execution and fallback handling."""
        self._logger.info(f"Running turn for message: {inbound.id}")
        (
            runtime,
            inbound,
            plugin_context,
            tool_call_strategy,
            service_port,
            executor,
        ) = _prepare_turn_runtime(
            self,
            inbound=inbound,
            history=history,
            progress_callback=progress_callback,
            approval_callback=approval_callback,
        )

        def _finalize_response(response: AgentResponse) -> AgentResponse:
            return finalize_turn_response(
                self,
                runtime,
                response,
                inbound=inbound,
                plugin_context=plugin_context,
            )

        _prepare_self_improvement_metadata(self, runtime)

        plan = _build_and_apply_tool_plan(
            service_port,
            inbound=inbound,
            runtime=runtime,
            forced_tools=forced_tools,
            capability_category=capability_category,
        )
        intent_category = plan.intent_category
        effective_forced_tools = plan.effective_forced_tools
        fallback_chain = plan.fallback_chain
        capability_primary = plan.capability_primary

        unavailable = self._build_unavailable_response(
            finalize_response=_finalize_response,
            plan=plan,
            intent_category=intent_category,
            inbound=inbound,
        )
        if unavailable is not None:
            return unavailable

        if capability_primary is None and effective_forced_tools:
            capability_primary = effective_forced_tools[0]

        tool_budget_state = (
            ToolBudgetState() if self._security_policy is not None else None
        )
        executor_deps = _build_executor_deps(self, _finalize_response)
        required_outcome = await _run_required_lane(
            executor=executor,
            intent_category=intent_category,
            effective_forced_tools=effective_forced_tools,
            fallback_chain=fallback_chain,
            capability_primary=capability_primary,
            tool_call_strategy=tool_call_strategy,
            tool_budget_state=tool_budget_state,
            executor_deps=executor_deps,
        )
        attempted_tools = list(required_outcome.attempted_tools)
        capability_fallback_trigger_reason = (
            required_outcome.capability_fallback_trigger_reason
        )
        if required_outcome.response is not None:
            return required_outcome.response

        return await _complete_unforced_lane(
            self,
            executor=executor,
            runtime=runtime,
            inbound=inbound,
            intent_category=intent_category,
            capability_primary=capability_primary,
            fallback_chain=fallback_chain,
            attempted_tools=attempted_tools,
            capability_fallback_trigger_reason=capability_fallback_trigger_reason,
            tool_call_strategy=tool_call_strategy,
            tool_budget_state=tool_budget_state,
            executor_deps=executor_deps,
            finalize_response=_finalize_response,
        )
