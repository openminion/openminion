"""Required-lane final-response request construction and retries."""

from typing import TYPE_CHECKING, Any

from openminion.modules.llm.contracts import (
    detect_raw_envelope,
    detect_raw_tool_markup,
)
from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.services.agent.constants import DEFAULT_TOOL_LOOP_CONTINUE_PROMPT
from openminion.services.agent.execution.finalization import (
    FINALIZATION_STATUS_FOLLOW_UP_GUIDANCE,
    FINALIZATION_STATUS_RETRY_GUIDANCE,
    requires_typed_finalization_contract,
)

from ..dependencies import ExecutorDeps
from ..followup import available_follow_up_tools, recover_text_tool_calls
from ..ports import ProviderHistoryMessage, ProviderRequest, ProviderResponse
from ..prompts import (
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
from .state import CompletionContext
from .unavailable import unavailable_discovery_retry_instruction
from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS

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


def tool_feedback_context(
    *, deps: ExecutorDeps, response: ProviderResponse, batch: ToolExecutionBatch
) -> tuple[str, str, bool]:
    payload = deps.tool_batch_metadata(
        batch=batch,
        tool_calls_count=len(response.tool_calls or []),
    ).get("tool_results", "[]")
    requires_status = requires_typed_finalization_contract(batch)
    message = build_tool_execution_results_message(
        payload=str(payload),
        finalization_guidance=(
            FINALIZATION_STATUS_FOLLOW_UP_GUIDANCE if requires_status else ""
        ),
    )
    return str(payload), message, requires_status


def _pre_tool_draft_message(response: ProviderResponse) -> ProviderHistoryMessage:
    return ProviderHistoryMessage(
        role="assistant",
        content=build_pre_tool_draft_message_text(
            response_text=str(getattr(response, "text", "") or "")
        ),
    )


def _retry_request(
    runner: "RequiredLaneRunner",
    *,
    user_message: str,
    history: list[ProviderHistoryMessage],
    metadata: dict[str, Any] | None = None,
) -> ProviderRequest:
    return ProviderRequest(
        user_message=user_message,
        system_prompt=runner.runtime.system_prompt,
        history=[*runner.runtime.provider_history, *history],
        tools=available_follow_up_tools(runner),
        metadata={"identity_context": "retained", **(metadata or {})},
    )


def _with_finalization_guidance(message: str) -> str:
    return f"{message}\n\n{FINALIZATION_STATUS_RETRY_GUIDANCE}"


def _needs_plain_text_retry(response: ProviderResponse) -> bool:
    return not response.tool_calls and _looks_like_embedded_tool_response_text(
        getattr(response, "text", "")
    )


def _looks_like_pre_tool_draft_echo(
    *, response: ProviderResponse, final_response: ProviderResponse
) -> bool:
    if final_response.tool_calls:
        return False
    pre_tool_text = str(getattr(response, "text", "") or "").strip()
    final_text = str(getattr(final_response, "text", "") or "").strip()
    return bool(pre_tool_text and final_text and final_text == pre_tool_text)


async def _call_initial_final_response(
    runner: "RequiredLaneRunner",
    *,
    response: ProviderResponse,
    tool_feedback_message: str,
    tool_call_strategy: str,
) -> ProviderResponse:
    final_response = await runner.runtime_ops.call_provider(
        _retry_request(
            runner,
            user_message=DEFAULT_TOOL_LOOP_CONTINUE_PROMPT,
            history=[
                _pre_tool_draft_message(response),
                ProviderHistoryMessage(role="user", content=tool_feedback_message),
            ],
        ),
        tool_call_strategy=tool_call_strategy,
    )
    return recover_text_tool_calls(runner, response=final_response)


async def _retry_plain_text_final_response(
    runner: "RequiredLaneRunner",
    *,
    final_response: ProviderResponse,
    tool_feedback_payload: str,
    tool_feedback_message: str,
    requires_finalization_status: bool,
    context: CompletionContext,
) -> ProviderResponse:
    if not _needs_plain_text_retry(final_response):
        return final_response
    retry_message = build_plain_text_retry_feedback(payload=tool_feedback_payload)
    if requires_finalization_status:
        retry_message = _with_finalization_guidance(retry_message)
    final_response = await runner.runtime_ops.call_provider(
        _retry_request(
            runner,
            user_message=build_plain_text_retry_user_message(
                base_prompt=DEFAULT_TOOL_LOOP_CONTINUE_PROMPT
            ),
            history=[
                _pre_tool_draft_message(context.response),
                ProviderHistoryMessage(role="user", content=tool_feedback_message),
                ProviderHistoryMessage(
                    role="assistant",
                    content=str(getattr(final_response, "text", "") or ""),
                ),
                ProviderHistoryMessage(role="user", content=retry_message),
            ],
        ),
        tool_call_strategy=context.tool_call_strategy,
    )
    final_response = recover_text_tool_calls(runner, response=final_response)
    if not _needs_plain_text_retry(final_response):
        return final_response
    final_response = await runner.runtime_ops.call_provider(
        _retry_request(
            runner,
            user_message=build_tool_envelope_retry_user_message(
                base_prompt=DEFAULT_TOOL_LOOP_CONTINUE_PROMPT
            ),
            history=[
                _pre_tool_draft_message(context.response),
                ProviderHistoryMessage(role="user", content=retry_message),
            ],
        ),
        tool_call_strategy=context.tool_call_strategy,
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
        response=response, final_response=final_response
    ):
        return final_response
    retry_message = build_stale_draft_retry_feedback(payload=tool_feedback_payload)
    if requires_finalization_status:
        retry_message = _with_finalization_guidance(retry_message)
    final_response = await runner.runtime_ops.call_provider(
        _retry_request(
            runner,
            user_message=build_stale_draft_retry_user_message(
                base_prompt=DEFAULT_TOOL_LOOP_CONTINUE_PROMPT
            ),
            history=[
                _pre_tool_draft_message(response),
                ProviderHistoryMessage(role="user", content=tool_feedback_message),
                ProviderHistoryMessage(
                    role="assistant",
                    content=str(getattr(final_response, "text", "") or ""),
                ),
                ProviderHistoryMessage(role="user", content=retry_message),
            ],
        ),
        tool_call_strategy=tool_call_strategy,
    )
    return recover_text_tool_calls(runner, response=final_response)


async def retry_finalization_status_response(
    runner: "RequiredLaneRunner",
    *,
    final_response: ProviderResponse,
    tool_feedback_payload: str,
    tool_feedback_message: str,
    context: CompletionContext,
) -> tuple[ProviderResponse, bool]:
    if final_response.tool_calls or bool(
        getattr(final_response, STATE_KEY_FINALIZATION_STATUS, None)
    ):
        return final_response, True
    retry_message = build_finalization_status_retry_feedback(
        payload=tool_feedback_payload,
        guidance=FINALIZATION_STATUS_RETRY_GUIDANCE,
    )
    final_response = await runner.runtime_ops.call_provider(
        _retry_request(
            runner,
            user_message=build_finalization_status_retry_user_message(
                base_prompt=DEFAULT_TOOL_LOOP_CONTINUE_PROMPT,
                guidance=FINALIZATION_STATUS_RETRY_GUIDANCE,
            ),
            history=[
                _pre_tool_draft_message(context.response),
                ProviderHistoryMessage(role="user", content=tool_feedback_message),
                ProviderHistoryMessage(
                    role="assistant",
                    content=str(getattr(final_response, "text", "") or ""),
                ),
                ProviderHistoryMessage(role="user", content=retry_message),
            ],
        ),
        tool_call_strategy=context.tool_call_strategy,
    )
    final_response = recover_text_tool_calls(runner, response=final_response)
    if final_response.tool_calls or bool(
        getattr(final_response, STATE_KEY_FINALIZATION_STATUS, None)
    ):
        return final_response, True
    return final_response, False


async def retry_duplicate_final_tool_calls_response(
    runner: "RequiredLaneRunner",
    *,
    deps: ExecutorDeps,
    final_response: ProviderResponse,
    context: CompletionContext,
) -> ProviderResponse:
    payload = deps.tool_batch_metadata(
        batch=context.batch,
        tool_calls_count=len(context.response.tool_calls or []),
    ).get("tool_results", "[]")
    retry_message = build_duplicate_final_tool_call_feedback(
        payload=str(payload),
        unavailable_instruction=unavailable_discovery_retry_instruction(
            context.response, context.batch
        ),
    )
    retry_response = await runner.runtime_ops.call_provider(
        _retry_request(
            runner,
            user_message=build_duplicate_final_tool_call_user_message(
                base_prompt=DEFAULT_TOOL_LOOP_CONTINUE_PROMPT
            ),
            history=[
                _pre_tool_draft_message(context.response),
                ProviderHistoryMessage(
                    role="user",
                    content=build_tool_execution_results_message(payload=str(payload)),
                ),
                ProviderHistoryMessage(
                    role="assistant",
                    content=str(getattr(final_response, "text", "") or ""),
                ),
                ProviderHistoryMessage(role="user", content=retry_message),
            ],
            metadata={"duplicate_tool_replan": "true"},
        ),
        tool_call_strategy=context.tool_call_strategy,
    )
    return recover_text_tool_calls(runner, response=retry_response)
