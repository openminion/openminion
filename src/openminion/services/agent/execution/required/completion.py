"""Required-lane post-execution coordination."""

from typing import TYPE_CHECKING, Any

from openminion.modules.llm.providers.base import ProviderResponse, ProviderToolCall
from openminion.modules.tool.registry import ToolExecutionBatch

from ..dependencies import ExecutorDeps
from ..response import tool_calls_payload
from ..validators import (
    extract_missing_argument_fields,
    is_tool_argument_error,
    looks_like_tool_call_envelope,
)
from .completion_retry import (
    _call_initial_final_response,
    _retry_plain_text_final_response,
    _retry_stale_draft_final_response,
    retry_finalization_status_response,
    tool_feedback_context,
)
from .completion_tools import (
    final_model_phase_result,
    finalization_contract_missing_result,
    handle_final_response_tool_calls,
)
from .metadata import (
    build_required_outcome,
    invalid_tool_arguments_metadata,
    resolved_tool_name,
    shared_capability_metadata,
)
from .state import (
    CompletionContext,
    RequiredLaneConfig,
    RequiredLaneState,
    _PhaseResult,
)

if TYPE_CHECKING:
    from .runner import RequiredLaneRunner


async def post_execution_follow_up_result(
    runner: "RequiredLaneRunner",
    *,
    deps: ExecutorDeps,
    context: CompletionContext,
) -> _PhaseResult:
    payload, message, requires_status = tool_feedback_context(
        deps=deps,
        response=context.response,
        batch=context.batch,
    )
    final_response = await _call_initial_final_response(
        runner,
        response=context.response,
        tool_feedback_message=message,
        tool_call_strategy=context.tool_call_strategy,
    )
    final_response = await _retry_plain_text_final_response(
        runner,
        final_response=final_response,
        tool_feedback_payload=payload,
        tool_feedback_message=message,
        requires_finalization_status=requires_status,
        context=context,
    )
    final_response = await _retry_stale_draft_final_response(
        runner,
        response=context.response,
        final_response=final_response,
        tool_feedback_payload=payload,
        tool_feedback_message=message,
        requires_finalization_status=requires_status,
        tool_call_strategy=context.tool_call_strategy,
    )
    if requires_status:
        final_response, has_status = await retry_finalization_status_response(
            runner,
            final_response=final_response,
            tool_feedback_payload=payload,
            tool_feedback_message=message,
            context=context,
        )
        if not has_status:
            return finalization_contract_missing_result(
                runner,
                deps=deps,
                final_response=final_response,
                context=context,
            )
    tool_result = await handle_final_response_tool_calls(
        runner,
        deps=deps,
        final_response=final_response,
        context=context,
    )
    if tool_result is not None:
        return tool_result
    return final_model_phase_result(
        runner,
        deps=deps,
        final_response=final_response,
        context=context,
    )


def _completion_context(
    state: RequiredLaneState,
    config: RequiredLaneConfig,
    *,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    all_attempts: list[ToolExecutionBatch],
) -> CompletionContext:
    tool_to_try = str(state.tool_to_try or "")
    attempted_tools = list(state.attempted_tools or [])
    return CompletionContext(
        response=response,
        batch=batch,
        intent_category=config.intent_category,
        tool_call_strategy=config.tool_call_strategy,
        tool_budget_state=config.tool_budget_state,
        attempted_tools=attempted_tools,
        capability_fallback_trigger_reason=state.capability_fallback_trigger_reason,
        tool_calls_sig=tool_calls_payload(response.tool_calls),
        shared_capability_meta=shared_capability_metadata(
            intent_category=config.intent_category,
            capability_primary=config.capability_primary,
            tool_to_try=tool_to_try,
            fallback_chain=config.fallback_chain,
            attempted_tools=attempted_tools,
            capability_fallback_trigger_reason=state.capability_fallback_trigger_reason,
            all_attempts_count=len(all_attempts),
        ),
    )


def _runtime_filled_result(
    runner: "RequiredLaneRunner",
    *,
    state: RequiredLaneState,
    deps: ExecutorDeps,
    context: CompletionContext,
) -> _PhaseResult:
    filled_args: dict[str, Any] = {}
    if context.response.tool_calls:
        raw_args = getattr(context.response.tool_calls[0], "arguments", None)
        if isinstance(raw_args, dict):
            filled_args = dict(raw_args)
    tool_to_try = str(state.tool_to_try or "")
    return _PhaseResult(
        action="return",
        outcome=build_required_outcome(
            runner,
            deps=deps,
            text=runner.runtime_ops._collect_batch_output(context.batch),
            model=str(getattr(context.response, "model", "") or ""),
            finish_reason="tool_direct",
            intent_category=context.intent_category,
            termination_reason="tool_direct",
            tool_calls_sig=tool_calls_payload(
                [
                    ProviderToolCall(
                        name=resolved_tool_name(tool_to_try, context.batch),
                        arguments=filled_args,
                        source="runtime_filled_args",
                    )
                ]
            ),
            batch=context.batch,
            tool_calls_count=len(context.response.tool_calls or []),
            attempted_tools=context.attempted_tools,
            capability_fallback_trigger_reason=context.capability_fallback_trigger_reason,
            extra_metadata={
                "capability_tool": tool_to_try,
                **dict(context.shared_capability_meta),
            },
        ),
    )


async def post_execution_success_result(
    runner: "RequiredLaneRunner",
    *,
    state: RequiredLaneState,
    deps: ExecutorDeps,
    config: RequiredLaneConfig,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    all_attempts: list[ToolExecutionBatch],
) -> _PhaseResult:
    context = _completion_context(
        state,
        config,
        response=response,
        batch=batch,
        all_attempts=all_attempts,
    )
    if state.runtime_args_filled:
        return _runtime_filled_result(runner, state=state, deps=deps, context=context)
    if looks_like_tool_call_envelope(response.text):
        return _PhaseResult(
            action="return",
            outcome=build_required_outcome(
                runner,
                deps=deps,
                text=runner.runtime_ops._collect_batch_output(batch),
                model=str(getattr(response, "model", "") or ""),
                finish_reason="tool_direct",
                intent_category=config.intent_category,
                termination_reason="tool_direct",
                tool_calls_sig=context.tool_calls_sig,
                batch=batch,
                tool_calls_count=len(response.tool_calls or []),
                attempted_tools=context.attempted_tools,
                capability_fallback_trigger_reason=context.capability_fallback_trigger_reason,
            ),
        )
    return await post_execution_follow_up_result(runner, deps=deps, context=context)


async def post_execution_argument_retry_result(
    runner: "RequiredLaneRunner",
    *,
    state: RequiredLaneState,
    deps: ExecutorDeps,
    config: RequiredLaneConfig,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    all_attempts: list[ToolExecutionBatch],
) -> _PhaseResult | None:
    if state.arg_retry_attempted or not any(
        is_tool_argument_error(result) for result in batch.results
    ):
        return None
    retry_response = await runner.runtime_ops.call_provider(
        state.request,
        tool_call_strategy=config.tool_call_strategy,
    )
    if (
        retry_response.tool_calls
        and runner.service_port.tools is not None
        and state.ctx is not None
    ):
        if getattr(state.ctx, "blast_radius_adapter", None) is None:
            from openminion.modules.policy.adapters.composition import (
                SEAM_AGENT_REQUIRED_LANE_RETRY,
                build_default_composition_boundary_adapter,
            )

            try:
                state.ctx.blast_radius_adapter = (
                    build_default_composition_boundary_adapter(
                        seam_id=SEAM_AGENT_REQUIRED_LANE_RETRY
                    )
                )
            except AttributeError:
                pass
        retry_batch = runner.service_port.tools.execute_calls(
            retry_response.tool_calls,
            context=state.ctx,
        )
        if not retry_batch.has_success and any(
            is_tool_argument_error(result) for result in retry_batch.results
        ):
            return _argument_retry_exhausted_result(
                runner,
                state=state,
                deps=deps,
                config=config,
                retry_response=retry_response,
                retry_batch=retry_batch,
            )
    return _PhaseResult(
        action="break",
        state_updates={
            "all_attempts": all_attempts,
            "arg_retry_attempted": True,
            "termination_reason": "tool_no_success",
        },
    )


def _argument_retry_exhausted_result(
    runner: "RequiredLaneRunner",
    *,
    state: RequiredLaneState,
    deps: ExecutorDeps,
    config: RequiredLaneConfig,
    retry_response: ProviderResponse,
    retry_batch: ToolExecutionBatch,
) -> _PhaseResult:
    missing_fields = extract_missing_argument_fields(list(retry_batch.results))
    tool_name = (
        retry_response.tool_calls[0].name
        if retry_response.tool_calls
        else str(state.tool_to_try or "")
    )
    runner.runtime_ops.record_argument_failure(
        tool_name=str(tool_name or ""),
        missing_fields=missing_fields,
        user_message=runner.runtime.user_message,
    )
    return _PhaseResult(
        action="return",
        outcome=build_required_outcome(
            runner,
            deps=deps,
            text="Invalid tool arguments",
            model=str(getattr(retry_response, "model", "") or ""),
            finish_reason="tool_calls",
            intent_category=config.intent_category,
            termination_reason=None,
            tool_calls_sig=None,
            batch=None,
            tool_calls_count=0,
            attempted_tools=list(state.attempted_tools or []),
            capability_fallback_trigger_reason=state.capability_fallback_trigger_reason,
            extra_metadata=invalid_tool_arguments_metadata(
                tool_name=str(tool_name or ""),
                missing_fields_csv=missing_fields,
            ),
        ),
    )


def post_execution_terminal_failure_result(
    runner: "RequiredLaneRunner",
    *,
    batch: ToolExecutionBatch,
    all_attempts: list[ToolExecutionBatch],
) -> _PhaseResult:
    first_error = batch.results[0].error if batch.results else "unknown"
    if runner.service_port.logger is not None:
        runner.service_port.logger.error(
            "Tool execution failed (non-eligible): %s", first_error
        )
    return _PhaseResult(
        action="break",
        state_updates={
            "all_attempts": all_attempts,
            "termination_reason": "tool_no_success",
        },
    )


async def phase_post_execution(
    runner: "RequiredLaneRunner",
    *,
    state: RequiredLaneState,
    deps: ExecutorDeps,
    config: RequiredLaneConfig,
) -> _PhaseResult:
    response = state.response
    batch = state.batch
    if response is None or batch is None:
        return _PhaseResult(
            action="break",
            state_updates={"termination_reason": "tool_no_success"},
        )
    all_attempts = [*list(state.all_attempts or []), batch]
    if batch.has_success:
        return await post_execution_success_result(
            runner,
            state=state,
            deps=deps,
            config=config,
            response=response,
            batch=batch,
            all_attempts=all_attempts,
        )
    retry_result = await post_execution_argument_retry_result(
        runner,
        state=state,
        deps=deps,
        config=config,
        response=response,
        batch=batch,
        all_attempts=all_attempts,
    )
    if retry_result is not None:
        return retry_result
    return post_execution_terminal_failure_result(
        runner,
        batch=batch,
        all_attempts=all_attempts,
    )


__all__ = ["phase_post_execution"]
