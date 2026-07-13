"""Unforced agent tool-call loop."""

from openminion.base.types import AgentResponse
from openminion.modules.llm.providers.base import ProviderResponse
from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.services.agent.constants import NO_PROGRESS_FAILURE_THRESHOLD
from openminion.services.security.policy import ToolBudgetState

from ..dependencies import ExecutorDeps
from ..followup import recover_text_tool_calls
from ..prompts import build_tool_argument_retry_feedback
from ..validators import (
    extract_missing_argument_fields,
    is_empty_provider_response,
    is_tool_argument_error,
)
from .followup import (
    LoopState,
    build_duplicate_tool_replan_request,
    build_follow_up_request,
    denied_tool_recovery_hint,
    finish_iteration,
)
from .metadata import (
    duplicate_tool_response,
    empty_provider_response_response,
    loop_no_progress_response,
    max_steps_response,
    model_final_response,
)


def _max_steps_for_runner(runner) -> int:
    return max(
        1,
        int(
            getattr(
                getattr(runner.service_port.config, "runtime", None),
                "agent_loop_max_steps",
                1,
            )
            or 1
        ),
    )


def _duplicate_signature_response(
    runner,
    *,
    deps: ExecutorDeps,
    state: LoopState,
    intent_category: str,
) -> tuple[str, AgentResponse | None]:
    from ..response import tool_calls_payload

    signature = tool_calls_payload(state.response.tool_calls)
    if signature in state.seen_signatures:
        return signature, duplicate_tool_response(
            runner,
            deps=deps,
            response=state.response,
            last_batch=state.last_batch,
            intent_category=intent_category,
            signature=signature,
        )
    state.seen_signatures.add(signature)
    return signature, None


async def _execute_batch(
    runner,
    *,
    response: ProviderResponse,
    tool_budget_state: ToolBudgetState | None,
) -> tuple[ToolExecutionBatch, list[dict[str, str]], bool]:
    batch, security_events, denied = await runner.runtime_ops.execute_tool_calls(
        response.tool_calls,
        tool_budget_state=tool_budget_state,
        context_metadata_overrides={"allow_runtime_direct": "true"},
    )
    runner.runtime_ops.record_self_improvement(
        user_message=runner.runtime.user_message,
        tool_results=batch.results,
    )
    return batch, security_events, denied


def _tool_argument_retry_feedback(batch: ToolExecutionBatch) -> str | None:
    if not any(is_tool_argument_error(result) for result in batch.results):
        return None
    return build_tool_argument_retry_feedback(
        missing_fields=extract_missing_argument_fields(list(batch.results))
    )


def _failure_signature(batch: ToolExecutionBatch) -> tuple[str, str] | None:
    for result in batch.results or []:
        if bool(getattr(result, "ok", False)):
            continue
        tool_name = str(getattr(result, "tool_name", "") or "").strip()
        if not tool_name:
            continue
        data = getattr(result, "data", {}) or {}
        error_payload = data.get("error") if isinstance(data, dict) else None
        error_code = str(
            (error_payload.get("code") if isinstance(error_payload, dict) else "")
            or (data.get("error_code") if isinstance(data, dict) else "")
            or getattr(result, "error", "")
            or "unknown_error"
        ).strip()
        return tool_name, error_code or "unknown_error"
    return None


async def _resolve_duplicate(
    runner,
    *,
    state: LoopState,
    deps: ExecutorDeps,
) -> tuple[bool, AgentResponse | None, str]:
    signature, duplicate = _duplicate_signature_response(
        runner, deps=deps, state=state, intent_category=state.intent_category
    )
    if duplicate is None:
        return False, None, signature
    if (
        state.duplicate_replan_attempted
        or state.last_batch is None
        or not state.last_batch.results
    ):
        return True, duplicate, signature
    state.duplicate_replan_attempted = True
    state.response = await runner.runtime_ops.call_provider(
        build_duplicate_tool_replan_request(
            runner,
            response=state.response,
            last_batch=state.last_batch,
            signature=signature,
            deps=deps,
        ),
        tool_call_strategy=state.tool_call_strategy,
    )
    state.response = recover_text_tool_calls(runner, response=state.response)
    if state.response.tool_calls:
        return True, None, signature
    terminal = (
        empty_provider_response_response(
            runner,
            deps=deps,
            response=state.response,
            batch=state.last_batch,
            intent_category=state.intent_category,
            signature=signature,
        )
        if is_empty_provider_response(state.response)
        else model_final_response(
            runner,
            deps=deps,
            initial_response=state.initial_response,
            response=state.response,
            batch=state.last_batch,
            intent_category=state.intent_category,
            signature=signature,
        )
    )
    return True, terminal, signature


async def _request_failure_recovery(
    runner,
    *,
    state: LoopState,
    deps: ExecutorDeps,
    failure_signature: tuple[str, str] | None,
    failure_count: int,
) -> tuple[bool, AgentResponse | None]:
    batch = state.last_batch
    if batch is None:
        return False, None
    argument_feedback = (
        None
        if state.denied or state.tool_arg_retry_attempted
        else _tool_argument_retry_feedback(batch)
    )
    if argument_feedback:
        state.tool_arg_retry_attempted = True
        state.response = await runner.runtime_ops.call_provider(
            build_follow_up_request(
                runner,
                deps=deps,
                response=state.response,
                batch=batch,
                extra_tool_feedback=argument_feedback,
            ),
            tool_call_strategy=state.tool_call_strategy,
        )
        state.response = recover_text_tool_calls(runner, response=state.response)
        if state.response.tool_calls:
            return True, None
    recovery_hint = (
        None if state.denied_recovery_attempted else denied_tool_recovery_hint(batch)
    )
    if recovery_hint:
        state.denied_recovery_attempted = True
        state.response = await runner.runtime_ops.call_provider(
            build_follow_up_request(
                runner,
                deps=deps,
                response=state.response,
                batch=batch,
                extra_tool_feedback=recovery_hint,
            ),
            tool_call_strategy=state.tool_call_strategy,
        )
        state.response = recover_text_tool_calls(runner, response=state.response)
        if state.response.tool_calls:
            return True, None
    if failure_signature and failure_count >= NO_PROGRESS_FAILURE_THRESHOLD:
        tool_name, error_code = failure_signature
        return True, loop_no_progress_response(
            runner,
            deps=deps,
            response=state.response,
            batch=batch,
            intent_category=state.intent_category,
            signature=state.signature,
            tool_name=tool_name,
            error_code=error_code,
            count=failure_count,
            threshold=NO_PROGRESS_FAILURE_THRESHOLD,
        )
    return False, None


async def handle_unforced_tool_calls(
    runner,
    *,
    initial_response: ProviderResponse,
    intent_category: str,
    tool_call_strategy: str,
    tool_budget_state: ToolBudgetState | None,
    deps: ExecutorDeps,
) -> AgentResponse:
    state = LoopState(
        initial_response=initial_response,
        response=initial_response,
        intent_category=intent_category,
        tool_call_strategy=tool_call_strategy,
        tool_budget_state=tool_budget_state,
    )
    max_steps = _max_steps_for_runner(runner)
    while state.step < max_steps:
        state.step += 1
        if not state.response.tool_calls:
            break
        handled, terminal, signature = await _resolve_duplicate(
            runner,
            state=state,
            deps=deps,
        )
        state.signature = signature
        if handled:
            if terminal is not None:
                return terminal
            continue
        batch, state.security_events, state.denied = await _execute_batch(
            runner,
            response=state.response,
            tool_budget_state=tool_budget_state,
        )
        state.last_batch = batch
        failure_signature = (
            _failure_signature(batch) if state.denied or not batch.has_success else None
        )
        failure_count = 0
        if failure_signature:
            failure_count = state.failure_counts.get(failure_signature, 0) + 1
            state.failure_counts[failure_signature] = failure_count
        if state.denied or not batch.has_success:
            handled, terminal = await _request_failure_recovery(
                runner,
                state=state,
                deps=deps,
                failure_signature=failure_signature,
                failure_count=failure_count,
            )
            if handled:
                if terminal is not None:
                    return terminal
                continue
        terminal = await finish_iteration(
            runner,
            state=state,
            deps=deps,
        )
        if terminal is not None:
            return terminal
    return max_steps_response(
        runner,
        deps=deps,
        response=state.response,
        last_batch=state.last_batch,
        intent_category=intent_category,
    )
