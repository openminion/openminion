"""Required-lane final tool-call resolution."""

import json
from typing import TYPE_CHECKING, Any

from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.services.agent.execution.finalization import (
    finalization_status_metadata,
    finalization_status_termination_reason,
)

from ..dependencies import ExecutorDeps
from ..ports import ProviderResponse
from ..response import tool_calls_payload
from ..validators import is_empty_provider_response
from .completion_retry import retry_duplicate_final_tool_calls_response
from .metadata import build_required_outcome
from .state import CompletionContext, _PhaseResult
from .unavailable import unavailable_discovery_phase_result

if TYPE_CHECKING:
    from .runner import RequiredLaneRunner


def _context_result(
    runner: "RequiredLaneRunner",
    *,
    deps: ExecutorDeps,
    context: CompletionContext,
    final_response: ProviderResponse,
    text: str,
    termination_reason: str,
    extra_metadata: dict[str, Any] | None = None,
) -> _PhaseResult:
    return _PhaseResult(
        action="return",
        outcome=build_required_outcome(
            runner,
            deps=deps,
            text=text,
            model=str(getattr(final_response, "model", "") or ""),
            finish_reason=str(getattr(final_response, "finish_reason", "") or "stop"),
            intent_category=context.intent_category,
            termination_reason=termination_reason,
            tool_calls_sig=context.tool_calls_sig,
            batch=context.batch,
            tool_calls_count=len(context.response.tool_calls or []),
            attempted_tools=context.attempted_tools,
            capability_fallback_trigger_reason=context.capability_fallback_trigger_reason,
            extra_metadata=(
                dict(context.shared_capability_meta)
                if extra_metadata is None
                else extra_metadata
            ),
        ),
    )


def finalization_contract_missing_result(
    runner: "RequiredLaneRunner",
    *,
    deps: ExecutorDeps,
    final_response: ProviderResponse,
    context: CompletionContext,
) -> _PhaseResult:
    return _context_result(
        runner,
        deps=deps,
        context=context,
        final_response=final_response,
        text=(
            "Substantive tool-backed work ended without the required typed "
            "finalization_status contract."
        ),
        termination_reason="finalization_contract_missing",
    )


def _duplicate_result(
    runner: "RequiredLaneRunner",
    *,
    deps: ExecutorDeps,
    final_response: ProviderResponse,
    context: CompletionContext,
) -> _PhaseResult:
    return _context_result(
        runner,
        deps=deps,
        context=context,
        final_response=final_response,
        text=runner.runtime_ops._collect_batch_output(context.batch),
        termination_reason="duplicate_tool_calls",
    )


def final_model_phase_result(
    runner: "RequiredLaneRunner",
    *,
    deps: ExecutorDeps,
    final_response: ProviderResponse,
    context: CompletionContext,
) -> _PhaseResult:
    empty = is_empty_provider_response(final_response)
    extra: dict[str, Any] = dict(context.shared_capability_meta)
    if empty:
        text = "Provider returned an empty response with no tool calls or finalization status."
        termination_reason = "empty_provider_response"
        extra["error_code"] = "EMPTY_PROVIDER_RESPONSE"
    else:
        text = str(getattr(final_response, "text", "") or "")
        termination_reason = finalization_status_termination_reason(
            final_response, default="model_final"
        )
        extra.update(finalization_status_metadata(final_response))
    return _context_result(
        runner,
        deps=deps,
        context=context,
        final_response=final_response,
        text=text,
        termination_reason=termination_reason,
        extra_metadata=extra,
    )


def _failed_follow_up_result(
    runner: "RequiredLaneRunner",
    *,
    deps: ExecutorDeps,
    final_response: ProviderResponse,
    final_sig: str,
    combined_batch: ToolExecutionBatch,
    security_events: list[dict[str, str]],
    context: CompletionContext,
) -> _PhaseResult:
    extra: dict[str, Any] = {}
    if security_events:
        extra["security_events"] = json.dumps(security_events, sort_keys=True)
    if context.tool_budget_state is not None:
        extra["tool_budget"] = json.dumps(
            context.tool_budget_state.snapshot(), sort_keys=True
        )
    return _PhaseResult(
        action="return",
        outcome=build_required_outcome(
            runner,
            deps=deps,
            text="status=error: tool execution blocked",
            model=str(getattr(final_response, "model", "") or ""),
            finish_reason=str(
                getattr(final_response, "finish_reason", "") or "tool_calls"
            ),
            intent_category=context.intent_category,
            termination_reason="tool_no_success",
            tool_calls_sig=final_sig,
            batch=combined_batch,
            tool_calls_count=len(final_response.tool_calls or []),
            attempted_tools=context.attempted_tools,
            capability_fallback_trigger_reason=context.capability_fallback_trigger_reason,
            extra_metadata=extra,
        ),
    )


async def handle_final_response_tool_calls(
    runner: "RequiredLaneRunner",
    *,
    deps: ExecutorDeps,
    final_response: ProviderResponse,
    context: CompletionContext,
) -> _PhaseResult | None:
    if not final_response.tool_calls:
        return None
    final_sig = tool_calls_payload(final_response.tool_calls)
    if final_sig == context.tool_calls_sig:
        final_response = await retry_duplicate_final_tool_calls_response(
            runner,
            deps=deps,
            final_response=final_response,
            context=context,
        )
        if not final_response.tool_calls:
            return final_model_phase_result(
                runner, deps=deps, final_response=final_response, context=context
            )
        final_sig = tool_calls_payload(final_response.tool_calls)
        if final_sig == context.tool_calls_sig:
            unavailable = unavailable_discovery_phase_result(
                runner,
                deps=deps,
                final_response=final_response,
                final_sig=final_sig,
                intent_category=context.intent_category,
                response=context.response,
                batch=context.batch,
                attempted_tools=context.attempted_tools,
                capability_fallback_trigger_reason=context.capability_fallback_trigger_reason,
                shared_capability_meta=context.shared_capability_meta,
            )
            return unavailable or _duplicate_result(
                runner,
                deps=deps,
                final_response=final_response,
                context=context,
            )
    follow_batch, security_events, denied = await runner.runtime_ops.execute_tool_calls(
        final_response.tool_calls,
        tool_budget_state=context.tool_budget_state,
    )
    runner.runtime_ops.record_self_improvement(
        user_message=runner.runtime.user_message,
        tool_results=follow_batch.results,
    )
    combined = ToolExecutionBatch(
        results=[*list(context.batch.results), *list(follow_batch.results)]
    )
    if denied or not follow_batch.has_success:
        return _failed_follow_up_result(
            runner,
            deps=deps,
            final_response=final_response,
            final_sig=final_sig,
            combined_batch=combined,
            security_events=security_events,
            context=context,
        )
    return _PhaseResult(
        action="return",
        outcome=build_required_outcome(
            runner,
            deps=deps,
            text=runner.runtime_ops._collect_batch_output(follow_batch),
            model=str(getattr(final_response, "model", "") or ""),
            finish_reason="tool_direct",
            intent_category=context.intent_category,
            termination_reason="tool_no_success",
            tool_calls_sig=final_sig,
            batch=combined,
            tool_calls_count=len(final_response.tool_calls or []),
            attempted_tools=context.attempted_tools,
            capability_fallback_trigger_reason=context.capability_fallback_trigger_reason,
        ),
    )
