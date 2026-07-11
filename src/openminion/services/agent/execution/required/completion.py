import json
from typing import TYPE_CHECKING, Any, Mapping

from openminion.modules.llm.providers.base import (
    ProviderHistoryMessage,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
)
from openminion.modules.llm.providers.tool_calling import (
    detect_raw_envelope,
    detect_raw_tool_markup,
)
from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.services.agent.constants import DEFAULT_TOOL_LOOP_CONTINUE_PROMPT
from openminion.services.agent.execution.finalization import (
    FINALIZATION_STATUS_FOLLOW_UP_GUIDANCE,
    FINALIZATION_STATUS_RETRY_GUIDANCE,
    finalization_status_metadata,
    finalization_status_termination_reason,
    requires_typed_finalization_contract,
)
from ...execution_prompts import (
    build_duplicate_final_tool_call_feedback,
    build_duplicate_final_tool_call_user_message,
    build_finalization_status_retry_feedback,
    build_finalization_status_retry_user_message,
    build_plain_text_retry_feedback,
    build_plain_text_retry_user_message,
    build_pre_tool_draft_message_text,
    build_stale_draft_retry_feedback,
    build_stale_draft_retry_user_message,
    build_tool_envelope_retry_user_message,
    build_tool_execution_results_message,
)
from openminion.services.security.policy import ToolBudgetState

from ..dependencies import ExecutorDeps
from ..followup import available_follow_up_tools, recover_text_tool_calls
from ..validators import is_empty_provider_response
from .metadata import (
    build_required_outcome,
    invalid_tool_arguments_metadata,
    resolved_tool_name,
    shared_capability_metadata,
)
from .state import RequiredLaneState, _PhaseResult
from .unavailable import (
    unavailable_discovery_phase_result,
    unavailable_discovery_retry_instruction,
)
from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS  # noqa: F401  (re-exported for in-module callers)

if TYPE_CHECKING:
    from .runner import RequiredLaneRunner


def _looks_like_embedded_tool_response_text(text: str | None) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    return (
        "unexecutable_tool_envelope" in lowered
        or lowered.startswith("<invoke")
        or "minimax:tool_call" in lowered
        or (
            normalized.startswith("```")
            and '"tool"' in lowered
            and ('"path"' in lowered or '"query"' in lowered)
        )
        or detect_raw_envelope(normalized)
        or detect_raw_tool_markup(normalized)
    )


def _tool_feedback_context(
    *, deps: ExecutorDeps, response: ProviderResponse, batch: ToolExecutionBatch
) -> tuple[str, str, bool]:
    payload = deps.tool_batch_metadata(
        batch=batch,
        tool_calls_count=len(response.tool_calls or []),
    ).get("tool_results", "[]")
    requires_status = requires_typed_finalization_contract(batch)
    finalization_guidance = (
        FINALIZATION_STATUS_FOLLOW_UP_GUIDANCE if requires_status else ""
    )
    message = build_tool_execution_results_message(
        payload=str(payload),
        finalization_guidance=finalization_guidance,
    )
    return str(payload), message, requires_status


def _pre_tool_draft_message(response: ProviderResponse) -> ProviderHistoryMessage:
    return ProviderHistoryMessage(
        role="assistant",
        content=build_pre_tool_draft_message_text(
            response_text=str(getattr(response, "text", "") or "")
        ),
    )


def _with_finalization_guidance(message: str) -> str:
    return f"{message}\n\n{FINALIZATION_STATUS_RETRY_GUIDANCE}"


def _needs_plain_text_retry(response: ProviderResponse) -> bool:
    if response.tool_calls:
        return False
    text = str(getattr(response, "text", "") or "")
    return _looks_like_embedded_tool_response_text(text)


def _looks_like_pre_tool_draft_echo(
    *,
    response: ProviderResponse,
    final_response: ProviderResponse,
) -> bool:
    if final_response.tool_calls:
        return False
    pre_tool_text = str(getattr(response, "text", "") or "").strip()
    final_text = str(getattr(final_response, "text", "") or "").strip()
    if not pre_tool_text or not final_text:
        return False
    return final_text == pre_tool_text


async def _call_initial_final_response(
    runner: "RequiredLaneRunner",
    *,
    response: ProviderResponse,
    tool_feedback_message: str,
    tool_call_strategy: str,
) -> ProviderResponse:
    final_response = await runner.runtime_ops.call_provider(
        ProviderRequest(
            user_message=DEFAULT_TOOL_LOOP_CONTINUE_PROMPT,
            system_prompt=runner.runtime.system_prompt,
            history=runner.runtime.provider_history
            + [
                _pre_tool_draft_message(response),
                ProviderHistoryMessage(role="user", content=tool_feedback_message),
            ],
            tools=available_follow_up_tools(runner),
            metadata={"identity_context": "retained"},
        ),
        tool_call_strategy=tool_call_strategy,
    )
    return recover_text_tool_calls(runner, response=final_response)


async def _retry_plain_text_final_response(
    runner: "RequiredLaneRunner",
    *,
    response: ProviderResponse,
    final_response: ProviderResponse,
    tool_feedback_payload: str,
    tool_feedback_message: str,
    requires_finalization_status: bool,
    tool_call_strategy: str,
) -> ProviderResponse:
    if not _needs_plain_text_retry(final_response):
        return final_response
    retry_user_message = build_plain_text_retry_feedback(payload=tool_feedback_payload)
    if requires_finalization_status:
        retry_user_message = _with_finalization_guidance(retry_user_message)
    final_response = await runner.runtime_ops.call_provider(
        ProviderRequest(
            user_message=build_plain_text_retry_user_message(
                base_prompt=DEFAULT_TOOL_LOOP_CONTINUE_PROMPT
            ),
            system_prompt=runner.runtime.system_prompt,
            history=runner.runtime.provider_history
            + [
                _pre_tool_draft_message(response),
                ProviderHistoryMessage(role="user", content=tool_feedback_message),
                ProviderHistoryMessage(
                    role="assistant",
                    content=str(getattr(final_response, "text", "") or ""),
                ),
                ProviderHistoryMessage(role="user", content=retry_user_message),
            ],
            tools=available_follow_up_tools(runner),
            metadata={"identity_context": "retained"},
        ),
        tool_call_strategy=tool_call_strategy,
    )
    final_response = recover_text_tool_calls(runner, response=final_response)
    if not _needs_plain_text_retry(final_response):
        return final_response
    final_response = await runner.runtime_ops.call_provider(
        ProviderRequest(
            user_message=build_tool_envelope_retry_user_message(
                base_prompt=DEFAULT_TOOL_LOOP_CONTINUE_PROMPT
            ),
            system_prompt=runner.runtime.system_prompt,
            history=runner.runtime.provider_history
            + [
                _pre_tool_draft_message(response),
                ProviderHistoryMessage(role="user", content=retry_user_message),
            ],
            tools=available_follow_up_tools(runner),
            metadata={"identity_context": "retained"},
        ),
        tool_call_strategy=tool_call_strategy,
    )
    return recover_text_tool_calls(runner, response=final_response)


async def _retry_stale_draft_final_response(
    runner: "RequiredLaneRunner",
    *,
    response: ProviderResponse,
    final_response: ProviderResponse,
    tool_feedback_payload: str,
    tool_feedback_message: str,
    requires_finalization_status: bool,
    tool_call_strategy: str,
) -> ProviderResponse:
    if not _looks_like_pre_tool_draft_echo(
        response=response,
        final_response=final_response,
    ):
        return final_response
    retry_user_message = build_stale_draft_retry_feedback(payload=tool_feedback_payload)
    if requires_finalization_status:
        retry_user_message = _with_finalization_guidance(retry_user_message)
    final_response = await runner.runtime_ops.call_provider(
        ProviderRequest(
            user_message=build_stale_draft_retry_user_message(
                base_prompt=DEFAULT_TOOL_LOOP_CONTINUE_PROMPT
            ),
            system_prompt=runner.runtime.system_prompt,
            history=runner.runtime.provider_history
            + [
                _pre_tool_draft_message(response),
                ProviderHistoryMessage(role="user", content=tool_feedback_message),
                ProviderHistoryMessage(
                    role="assistant",
                    content=str(getattr(final_response, "text", "") or ""),
                ),
                ProviderHistoryMessage(role="user", content=retry_user_message),
            ],
            tools=available_follow_up_tools(runner),
            metadata={"identity_context": "retained"},
        ),
        tool_call_strategy=tool_call_strategy,
    )
    return recover_text_tool_calls(runner, response=final_response)


def _finalization_contract_missing_result(
    runner: "RequiredLaneRunner",
    *,
    deps: ExecutorDeps,
    final_response: ProviderResponse,
    intent_category: str,
    tool_calls_sig: str,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    attempted_tools: list[str],
    capability_fallback_trigger_reason: str | None,
    shared_capability_meta: Mapping[str, Any],
) -> _PhaseResult:
    return _PhaseResult(
        action="return",
        outcome=build_required_outcome(
            runner,
            deps=deps,
            text=(
                "Substantive tool-backed work ended without the required typed "
                "finalization_status contract."
            ),
            model=str(getattr(final_response, "model", "") or ""),
            finish_reason=str(getattr(final_response, "finish_reason", "") or "stop"),
            intent_category=intent_category,
            termination_reason="finalization_contract_missing",
            tool_calls_sig=tool_calls_sig,
            batch=batch,
            tool_calls_count=len(response.tool_calls or []),
            attempted_tools=attempted_tools,
            capability_fallback_trigger_reason=capability_fallback_trigger_reason,
            extra_metadata=dict(shared_capability_meta),
        ),
    )


async def _retry_finalization_status_response(
    runner: "RequiredLaneRunner",
    *,
    deps: ExecutorDeps,
    response: ProviderResponse,
    final_response: ProviderResponse,
    tool_feedback_payload: str,
    tool_feedback_message: str,
    tool_call_strategy: str,
    intent_category: str,
    tool_calls_sig: str,
    batch: ToolExecutionBatch,
    attempted_tools: list[str],
    capability_fallback_trigger_reason: str | None,
    shared_capability_meta: Mapping[str, Any],
) -> ProviderResponse | _PhaseResult:
    if final_response.tool_calls or bool(
        getattr(final_response, STATE_KEY_FINALIZATION_STATUS, None)
    ):
        return final_response
    retry_user_message = build_finalization_status_retry_feedback(
        payload=tool_feedback_payload,
        guidance=FINALIZATION_STATUS_RETRY_GUIDANCE,
    )
    final_response = await runner.runtime_ops.call_provider(
        ProviderRequest(
            user_message=build_finalization_status_retry_user_message(
                base_prompt=DEFAULT_TOOL_LOOP_CONTINUE_PROMPT,
                guidance=FINALIZATION_STATUS_RETRY_GUIDANCE,
            ),
            system_prompt=runner.runtime.system_prompt,
            history=runner.runtime.provider_history
            + [
                _pre_tool_draft_message(response),
                ProviderHistoryMessage(role="user", content=tool_feedback_message),
                ProviderHistoryMessage(
                    role="assistant",
                    content=str(getattr(final_response, "text", "") or ""),
                ),
                ProviderHistoryMessage(role="user", content=retry_user_message),
            ],
            tools=available_follow_up_tools(runner),
            metadata={"identity_context": "retained"},
        ),
        tool_call_strategy=tool_call_strategy,
    )
    final_response = recover_text_tool_calls(runner, response=final_response)
    if final_response.tool_calls or bool(
        getattr(final_response, STATE_KEY_FINALIZATION_STATUS, None)
    ):
        return final_response
    return _finalization_contract_missing_result(
        runner,
        deps=deps,
        final_response=final_response,
        intent_category=intent_category,
        tool_calls_sig=tool_calls_sig,
        response=response,
        batch=batch,
        attempted_tools=attempted_tools,
        capability_fallback_trigger_reason=capability_fallback_trigger_reason,
        shared_capability_meta=shared_capability_meta,
    )


def _duplicate_final_tool_calls_result(
    runner: "RequiredLaneRunner",
    *,
    deps: ExecutorDeps,
    final_response: ProviderResponse,
    final_sig: str,
    intent_category: str,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    attempted_tools: list[str],
    capability_fallback_trigger_reason: str | None,
    shared_capability_meta: Mapping[str, Any],
) -> _PhaseResult:
    return _PhaseResult(
        action="return",
        outcome=build_required_outcome(
            runner,
            deps=deps,
            text=runner.runtime_ops._collect_batch_output(batch),
            model=str(getattr(final_response, "model", "") or ""),
            finish_reason=str(
                getattr(final_response, "finish_reason", "") or "tool_calls"
            ),
            intent_category=intent_category,
            termination_reason="duplicate_tool_calls",
            tool_calls_sig=final_sig,
            batch=batch,
            tool_calls_count=len(response.tool_calls or []),
            attempted_tools=attempted_tools,
            capability_fallback_trigger_reason=capability_fallback_trigger_reason,
            extra_metadata=dict(shared_capability_meta),
        ),
    )


async def _retry_duplicate_final_tool_calls_response(
    runner: "RequiredLaneRunner",
    *,
    deps: ExecutorDeps,
    response: ProviderResponse,
    final_response: ProviderResponse,
    batch: ToolExecutionBatch,
    tool_call_strategy: str,
) -> ProviderResponse:
    tool_feedback_payload = deps.tool_batch_metadata(
        batch=batch,
        tool_calls_count=len(response.tool_calls or []),
    ).get("tool_results", "[]")
    unavailable_instruction = unavailable_discovery_retry_instruction(response, batch)
    retry_user_message = build_duplicate_final_tool_call_feedback(
        payload=str(tool_feedback_payload),
        unavailable_instruction=unavailable_instruction or "",
    )
    retry_response = await runner.runtime_ops.call_provider(
        ProviderRequest(
            user_message=build_duplicate_final_tool_call_user_message(
                base_prompt=DEFAULT_TOOL_LOOP_CONTINUE_PROMPT
            ),
            system_prompt=runner.runtime.system_prompt,
            history=runner.runtime.provider_history
            + [
                _pre_tool_draft_message(response),
                ProviderHistoryMessage(
                    role="user",
                    content=build_tool_execution_results_message(
                        payload=str(tool_feedback_payload)
                    ),
                ),
                ProviderHistoryMessage(
                    role="assistant",
                    content=str(getattr(final_response, "text", "") or ""),
                ),
                ProviderHistoryMessage(role="user", content=retry_user_message),
            ],
            tools=available_follow_up_tools(runner),
            metadata={
                "identity_context": "retained",
                "duplicate_tool_replan": "true",
            },
        ),
        tool_call_strategy=tool_call_strategy,
    )
    return recover_text_tool_calls(runner, response=retry_response)


async def _handle_final_response_tool_calls(
    runner: "RequiredLaneRunner",
    *,
    deps: ExecutorDeps,
    final_response: ProviderResponse,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    tool_budget_state: ToolBudgetState | None,
    tool_call_strategy: str,
    intent_category: str,
    tool_calls_sig: str,
    attempted_tools: list[str],
    capability_fallback_trigger_reason: str | None,
    shared_capability_meta: Mapping[str, Any],
) -> _PhaseResult | None:
    if not final_response.tool_calls:
        return None
    final_sig = deps.tool_calls_payload(final_response.tool_calls)
    if final_sig == tool_calls_sig:
        retry_response = await _retry_duplicate_final_tool_calls_response(
            runner,
            deps=deps,
            response=response,
            final_response=final_response,
            batch=batch,
            tool_call_strategy=tool_call_strategy,
        )
        if not retry_response.tool_calls:
            return _final_model_phase_result(
                runner,
                deps=deps,
                final_response=retry_response,
                response=response,
                batch=batch,
                intent_category=intent_category,
                tool_calls_sig=tool_calls_sig,
                attempted_tools=attempted_tools,
                capability_fallback_trigger_reason=capability_fallback_trigger_reason,
                shared_capability_meta=shared_capability_meta,
            )
        final_response = retry_response
        final_sig = deps.tool_calls_payload(final_response.tool_calls)
        if final_sig == tool_calls_sig:
            unavailable_result = unavailable_discovery_phase_result(
                runner,
                deps=deps,
                final_response=final_response,
                final_sig=final_sig,
                intent_category=intent_category,
                response=response,
                batch=batch,
                attempted_tools=attempted_tools,
                capability_fallback_trigger_reason=capability_fallback_trigger_reason,
                shared_capability_meta=shared_capability_meta,
            )
            if unavailable_result is not None:
                return unavailable_result
            return _duplicate_final_tool_calls_result(
                runner,
                deps=deps,
                final_response=final_response,
                final_sig=final_sig,
                intent_category=intent_category,
                response=response,
                batch=batch,
                attempted_tools=attempted_tools,
                capability_fallback_trigger_reason=capability_fallback_trigger_reason,
                shared_capability_meta=shared_capability_meta,
            )
    (
        follow_batch,
        follow_security_events,
        follow_denied,
    ) = await runner.runtime_ops.execute_tool_calls(
        final_response.tool_calls,
        tool_budget_state=tool_budget_state,
    )
    runner.runtime_ops.record_self_improvement(
        user_message=runner.runtime.user_message,
        tool_results=follow_batch.results,
    )
    combined_batch = ToolExecutionBatch(
        results=list(batch.results) + list(follow_batch.results)
    )
    if follow_denied or not follow_batch.has_success:
        extra_metadata: dict[str, Any] = {}
        if follow_security_events:
            extra_metadata["security_events"] = json.dumps(
                follow_security_events, sort_keys=True
            )
        if tool_budget_state is not None:
            extra_metadata["tool_budget"] = json.dumps(
                tool_budget_state.snapshot(), sort_keys=True
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
                intent_category=intent_category,
                termination_reason="tool_no_success",
                tool_calls_sig=final_sig,
                batch=combined_batch,
                tool_calls_count=len(final_response.tool_calls or []),
                attempted_tools=attempted_tools,
                capability_fallback_trigger_reason=capability_fallback_trigger_reason,
                extra_metadata=extra_metadata,
            ),
        )
    return _PhaseResult(
        action="return",
        outcome=build_required_outcome(
            runner,
            deps=deps,
            text=runner.runtime_ops._collect_batch_output(follow_batch),
            model=str(getattr(final_response, "model", "") or ""),
            finish_reason="tool_direct",
            intent_category=intent_category,
            termination_reason="tool_no_success",
            tool_calls_sig=final_sig,
            batch=combined_batch,
            tool_calls_count=len(final_response.tool_calls or []),
            attempted_tools=attempted_tools,
            capability_fallback_trigger_reason=capability_fallback_trigger_reason,
        ),
    )


def _final_model_phase_result(
    runner: "RequiredLaneRunner",
    *,
    deps: ExecutorDeps,
    final_response: ProviderResponse,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    intent_category: str,
    tool_calls_sig: str,
    attempted_tools: list[str],
    capability_fallback_trigger_reason: str | None,
    shared_capability_meta: Mapping[str, Any],
) -> _PhaseResult:
    empty = is_empty_provider_response(final_response)
    text = (
        "Provider returned an empty response with no tool calls or finalization status."
        if empty
        else str(getattr(final_response, "text", "") or "")
    )
    termination_reason = (
        "empty_provider_response"
        if empty
        else finalization_status_termination_reason(
            final_response, default="model_final"
        )
    )
    extra: dict[str, Any] = {**dict(shared_capability_meta)}
    if empty:
        extra["error_code"] = "EMPTY_PROVIDER_RESPONSE"
    else:
        extra.update(finalization_status_metadata(final_response))
    return _PhaseResult(
        action="return",
        outcome=build_required_outcome(
            runner,
            deps=deps,
            text=text,
            model=str(getattr(final_response, "model", "") or ""),
            finish_reason=str(getattr(final_response, "finish_reason", "") or "stop"),
            intent_category=intent_category,
            termination_reason=termination_reason,
            tool_calls_sig=tool_calls_sig,
            batch=batch,
            tool_calls_count=len(response.tool_calls or []),
            attempted_tools=attempted_tools,
            capability_fallback_trigger_reason=capability_fallback_trigger_reason,
            extra_metadata=extra,
        ),
    )


async def post_execution_follow_up_result(
    runner: "RequiredLaneRunner",
    *,
    deps: ExecutorDeps,
    intent_category: str,
    tool_call_strategy: str,
    tool_budget_state: ToolBudgetState | None,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    attempted_tools: list[str],
    capability_fallback_trigger_reason: str | None,
    tool_calls_sig: str,
    shared_capability_meta: Mapping[str, Any],
) -> _PhaseResult:
    feedback_payload, feedback_message, requires_status = _tool_feedback_context(
        deps=deps,
        response=response,
        batch=batch,
    )
    final_response = await _call_initial_final_response(
        runner,
        response=response,
        tool_feedback_message=feedback_message,
        tool_call_strategy=tool_call_strategy,
    )
    final_response = await _retry_plain_text_final_response(
        runner,
        response=response,
        final_response=final_response,
        tool_feedback_payload=feedback_payload,
        tool_feedback_message=feedback_message,
        requires_finalization_status=requires_status,
        tool_call_strategy=tool_call_strategy,
    )
    final_response = await _retry_stale_draft_final_response(
        runner,
        response=response,
        final_response=final_response,
        tool_feedback_payload=feedback_payload,
        tool_feedback_message=feedback_message,
        requires_finalization_status=requires_status,
        tool_call_strategy=tool_call_strategy,
    )
    if requires_status:
        maybe_response = await _retry_finalization_status_response(
            runner,
            deps=deps,
            response=response,
            final_response=final_response,
            tool_feedback_payload=feedback_payload,
            tool_feedback_message=feedback_message,
            tool_call_strategy=tool_call_strategy,
            intent_category=intent_category,
            tool_calls_sig=tool_calls_sig,
            batch=batch,
            attempted_tools=attempted_tools,
            capability_fallback_trigger_reason=capability_fallback_trigger_reason,
            shared_capability_meta=shared_capability_meta,
        )
        if isinstance(maybe_response, _PhaseResult):
            return maybe_response
        final_response = maybe_response
    tool_call_result = await _handle_final_response_tool_calls(
        runner,
        deps=deps,
        final_response=final_response,
        response=response,
        batch=batch,
        tool_budget_state=tool_budget_state,
        tool_call_strategy=tool_call_strategy,
        intent_category=intent_category,
        tool_calls_sig=tool_calls_sig,
        attempted_tools=attempted_tools,
        capability_fallback_trigger_reason=capability_fallback_trigger_reason,
        shared_capability_meta=shared_capability_meta,
    )
    if tool_call_result is not None:
        return tool_call_result
    return _final_model_phase_result(
        runner,
        deps=deps,
        final_response=final_response,
        response=response,
        batch=batch,
        intent_category=intent_category,
        tool_calls_sig=tool_calls_sig,
        attempted_tools=attempted_tools,
        capability_fallback_trigger_reason=capability_fallback_trigger_reason,
        shared_capability_meta=shared_capability_meta,
    )


async def post_execution_success_result(
    runner: "RequiredLaneRunner",
    *,
    state: RequiredLaneState,
    deps: ExecutorDeps,
    intent_category: str,
    capability_primary: str | None,
    fallback_chain: list[str],
    tool_call_strategy: str,
    tool_budget_state: ToolBudgetState | None,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    all_attempts: list[ToolExecutionBatch],
    attempted_tools: list[str],
    capability_fallback_trigger_reason: str | None,
    tool_to_try: str,
) -> _PhaseResult:
    response_text = runner.runtime_ops._collect_batch_output(batch)
    tool_calls_sig = deps.tool_calls_payload(response.tool_calls)
    shared_capability_meta = shared_capability_metadata(
        intent_category=intent_category,
        capability_primary=capability_primary,
        tool_to_try=tool_to_try,
        fallback_chain=fallback_chain,
        attempted_tools=attempted_tools,
        capability_fallback_trigger_reason=capability_fallback_trigger_reason,
        all_attempts_count=len(all_attempts),
    )
    if state.runtime_args_filled:
        filled_args: dict[str, Any] = {}
        if response.tool_calls:
            raw_args = getattr(response.tool_calls[0], "arguments", None)
            if isinstance(raw_args, dict):
                filled_args = dict(raw_args)
        return _PhaseResult(
            action="return",
            outcome=build_required_outcome(
                runner,
                deps=deps,
                text=response_text,
                model=str(getattr(response, "model", "") or ""),
                finish_reason="tool_direct",
                intent_category=intent_category,
                termination_reason="tool_direct",
                tool_calls_sig=deps.tool_calls_payload(
                    [
                        ProviderToolCall(
                            name=resolved_tool_name(tool_to_try, batch),
                            arguments=filled_args,
                            source="runtime_filled_args",
                        )
                    ]
                ),
                batch=batch,
                tool_calls_count=len(response.tool_calls or []),
                attempted_tools=attempted_tools,
                capability_fallback_trigger_reason=capability_fallback_trigger_reason,
                extra_metadata={
                    "capability_tool": tool_to_try,
                    **shared_capability_meta,
                },
            ),
        )
    if deps.looks_like_tool_call_envelope(response.text):
        return _PhaseResult(
            action="return",
            outcome=build_required_outcome(
                runner,
                deps=deps,
                text=response_text,
                model=str(getattr(response, "model", "") or ""),
                finish_reason="tool_direct",
                intent_category=intent_category,
                termination_reason="tool_direct",
                tool_calls_sig=tool_calls_sig,
                batch=batch,
                tool_calls_count=len(response.tool_calls or []),
                attempted_tools=attempted_tools,
                capability_fallback_trigger_reason=capability_fallback_trigger_reason,
            ),
        )
    return await post_execution_follow_up_result(
        runner,
        deps=deps,
        intent_category=intent_category,
        tool_call_strategy=tool_call_strategy,
        tool_budget_state=tool_budget_state,
        response=response,
        batch=batch,
        attempted_tools=attempted_tools,
        capability_fallback_trigger_reason=capability_fallback_trigger_reason,
        tool_calls_sig=tool_calls_sig,
        shared_capability_meta=shared_capability_meta,
    )


async def post_execution_argument_retry_result(
    runner: "RequiredLaneRunner",
    *,
    state: RequiredLaneState,
    deps: ExecutorDeps,
    intent_category: str,
    tool_call_strategy: str,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    attempted_tools: list[str],
    capability_fallback_trigger_reason: str | None,
    tool_to_try: str,
    all_attempts: list[ToolExecutionBatch],
) -> _PhaseResult | None:
    if state.arg_retry_attempted or not any(
        deps.is_tool_argument_error(result) for result in batch.results
    ):
        return None

    retry_response = await runner.runtime_ops.call_provider(
        state.request,
        tool_call_strategy=tool_call_strategy,
    )
    if (
        retry_response.tool_calls
        and runner.service_port.tools is not None
        and state.ctx is not None
    ):
        if getattr(state.ctx, "blast_radius_adapter", None) is None:
            from openminion.services.security.blast_radius.wiring import (
                SEAM_AGENT_REQUIRED_LANE_RETRY,
                build_default_composition_boundary_adapter,
            )

            try:
                state.ctx.blast_radius_adapter = (
                    build_default_composition_boundary_adapter(
                        seam_id=SEAM_AGENT_REQUIRED_LANE_RETRY,
                    )
                )
            except AttributeError:
                pass
        retry_batch = runner.service_port.tools.execute_calls(
            retry_response.tool_calls,
            context=state.ctx,
        )
        if not retry_batch.has_success and any(
            deps.is_tool_argument_error(result) for result in retry_batch.results
        ):
            missing_fields = deps.extract_missing_argument_fields(
                list(retry_batch.results)
            )
            exhausted_tool = (
                retry_response.tool_calls[0].name
                if retry_response.tool_calls
                else tool_to_try
            )
            runner.runtime_ops.record_argument_failure(
                tool_name=str(exhausted_tool or ""),
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
                    intent_category=intent_category,
                    termination_reason=None,
                    tool_calls_sig=None,
                    batch=None,
                    tool_calls_count=0,
                    attempted_tools=attempted_tools,
                    capability_fallback_trigger_reason=capability_fallback_trigger_reason,
                    extra_metadata=invalid_tool_arguments_metadata(
                        tool_name=str(exhausted_tool or ""),
                        missing_fields_csv=missing_fields,
                    ),
                ),
            )
    return _PhaseResult(
        action="break",
        state_updates={
            "all_attempts": all_attempts,
            "arg_retry_attempted": True,
            "termination_reason": "tool_no_success",
        },
    )


def post_execution_terminal_failure_result(
    runner: "RequiredLaneRunner",
    *,
    batch: ToolExecutionBatch,
    all_attempts: list[ToolExecutionBatch],
) -> _PhaseResult:
    first_error = batch.results[0].error if batch.results else "unknown"
    logger = runner.service_port.logger
    if logger is not None:
        logger.error("Tool execution failed (non-eligible): %s", first_error)
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
    intent_category: str,
    capability_primary: str | None,
    fallback_chain: list[str],
    tool_call_strategy: str,
    tool_budget_state: ToolBudgetState | None,
) -> _PhaseResult:
    response = state.response
    batch = state.batch
    if response is None or batch is None:
        return _PhaseResult(
            action="break", state_updates={"termination_reason": "tool_no_success"}
        )

    tool_to_try = str(state.tool_to_try or "")
    all_attempts = list(state.all_attempts or [])
    all_attempts.append(batch)
    attempted_tools = list(state.attempted_tools or [])
    capability_fallback_trigger_reason = state.capability_fallback_trigger_reason

    if batch.has_success:
        return await post_execution_success_result(
            runner,
            state=state,
            deps=deps,
            intent_category=intent_category,
            capability_primary=capability_primary,
            fallback_chain=fallback_chain,
            tool_call_strategy=tool_call_strategy,
            tool_budget_state=tool_budget_state,
            response=response,
            batch=batch,
            all_attempts=all_attempts,
            attempted_tools=attempted_tools,
            capability_fallback_trigger_reason=capability_fallback_trigger_reason,
            tool_to_try=tool_to_try,
        )

    argument_retry_result = await post_execution_argument_retry_result(
        runner,
        state=state,
        deps=deps,
        intent_category=intent_category,
        tool_call_strategy=tool_call_strategy,
        response=response,
        batch=batch,
        attempted_tools=attempted_tools,
        capability_fallback_trigger_reason=capability_fallback_trigger_reason,
        tool_to_try=tool_to_try,
        all_attempts=all_attempts,
    )
    if argument_retry_result is not None:
        return argument_retry_result

    return post_execution_terminal_failure_result(
        runner, batch=batch, all_attempts=all_attempts
    )


__all__ = [
    "phase_post_execution",
    "post_execution_argument_retry_result",
    "post_execution_follow_up_result",
    "post_execution_success_result",
    "post_execution_terminal_failure_result",
]
