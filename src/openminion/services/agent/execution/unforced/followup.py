from dataclasses import dataclass, field

from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS
from openminion.base.types import AgentResponse
from openminion.modules.llm.providers.base import (
    ProviderHistoryMessage,
    ProviderRequest,
    ProviderResponse,
)
from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.services.agent.constants import (
    DEFAULT_TOOL_LOOP_CONTINUE_PROMPT,
)
from openminion.services.agent.execution.finalization import (
    FINALIZATION_STATUS_FOLLOW_UP_GUIDANCE,
    FINALIZATION_STATUS_RETRY_GUIDANCE,
    requires_typed_finalization_contract_for_results,
)
from openminion.services.agent.execution.followup import (
    available_follow_up_tools,
    recover_text_tool_calls,
)
from openminion.modules.policy import ToolBudgetState
from ..prompts import (
    build_denied_tool_recovery_hint,
    build_duplicate_tool_replan_feedback,
    build_duplicate_tool_replan_user_message,
    build_finalization_status_retry_feedback,
    build_plain_text_retry_feedback,
    build_plain_text_retry_user_message,
    build_pre_tool_draft_message_text,
    build_tool_execution_results_message,
)

from ..dependencies import ExecutorDeps
from ..validators import is_empty_provider_response, looks_like_tool_call_envelope
from .metadata import (
    blocked_tool_response,
    direct_tool_response,
    empty_provider_response_response,
    finalization_contract_missing_response,
    model_final_response,
)


@dataclass(slots=True)
class LoopState:
    initial_response: ProviderResponse
    response: ProviderResponse
    intent_category: str
    tool_call_strategy: str
    tool_budget_state: ToolBudgetState | None
    seen_signatures: set[str] = field(default_factory=set)
    last_batch: ToolExecutionBatch | None = None
    cumulative_tool_results: list[object] = field(default_factory=list)
    failure_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    denied_recovery_attempted: bool = False
    duplicate_replan_attempted: bool = False
    tool_arg_retry_attempted: bool = False
    signature: str = ""
    security_events: list[dict[str, str]] = field(default_factory=list)
    denied: bool = False
    step: int = 0


def build_follow_up_request(
    runner,
    *,
    deps: ExecutorDeps,
    response,
    batch: ToolExecutionBatch,
    require_typed_finalization: bool = False,
    extra_tool_feedback: str | None = None,
) -> ProviderRequest:
    tool_feedback_payload = deps.tool_batch_metadata(
        batch=batch,
        tool_calls_count=len(response.tool_calls or []),
    ).get("tool_results", "[]")
    tool_feedback_message = build_tool_execution_results_message(
        payload=str(tool_feedback_payload),
        extra_feedback=str(extra_tool_feedback or ""),
        finalization_guidance=(
            FINALIZATION_STATUS_FOLLOW_UP_GUIDANCE if require_typed_finalization else ""
        ),
    )
    tool_history_entry = ProviderHistoryMessage(
        role="user",
        content=tool_feedback_message,
    )
    return ProviderRequest(
        user_message=DEFAULT_TOOL_LOOP_CONTINUE_PROMPT,
        system_prompt=runner.runtime.system_prompt,
        history=runner.runtime.provider_history
        + [
            ProviderHistoryMessage(
                role="assistant",
                content=build_pre_tool_draft_message_text(
                    response_text=str(getattr(response, "text", "") or "")
                ),
            ),
            tool_history_entry,
        ],
        tools=available_follow_up_tools(runner),
        metadata={
            "identity_context": "retained",
        },
    )


def denied_tool_recovery_hint(batch: ToolExecutionBatch) -> str | None:
    def _tool_error_details(data: object) -> dict[str, object]:
        if not isinstance(data, dict):
            return {}
        raw_details = data.get("error_details")
        if isinstance(raw_details, dict):
            return dict(raw_details)
        raw_error = data.get("error")
        if isinstance(raw_error, dict):
            nested_details = raw_error.get("details")
            if isinstance(nested_details, dict):
                return dict(nested_details)
        return {}

    for result in list(getattr(batch, "results", []) or []):
        if bool(getattr(result, "ok", False)):
            continue
        data = getattr(result, "data", {})
        if not isinstance(data, dict):
            continue
        error_code = str(data.get("error_code", "") or "").strip().upper()
        details = _tool_error_details(data)
        if not details or error_code != "POLICY_DENIED":
            continue
        suggested_tool = str(details.get("suggested_tool", "") or "").strip()
        suggested_fix = str(details.get("suggested_fix", "") or "").strip()
        if not suggested_tool:
            continue
        blocked_tool = str(getattr(result, "tool_name", "") or "").strip() or "tool"
        return build_denied_tool_recovery_hint(
            blocked_tool=blocked_tool,
            suggested_tool=suggested_tool,
            suggested_fix=suggested_fix,
        )
    return None


def build_retry_request(
    runner,
    *,
    initial_response: ProviderResponse,
    response: ProviderResponse,
    follow_up_request: ProviderRequest,
    retry_user_message: str,
    request_user_message: str | None = None,
) -> ProviderRequest:
    return ProviderRequest(
        user_message=request_user_message or retry_user_message,
        system_prompt=runner.runtime.system_prompt,
        history=runner.runtime.provider_history
        + [
            ProviderHistoryMessage(
                role="assistant",
                content=str(getattr(initial_response, "text", "") or ""),
            ),
            ProviderHistoryMessage(role="user", content=follow_up_request.user_message),
            ProviderHistoryMessage(
                role="assistant",
                content=str(getattr(response, "text", "") or ""),
            ),
            ProviderHistoryMessage(role="user", content=retry_user_message),
        ],
        metadata={"identity_context": "retained"},
    )


def build_plain_text_retry_request(
    runner,
    *,
    initial_response: ProviderResponse,
    response: ProviderResponse,
    follow_up_request: ProviderRequest,
    tool_feedback_payload: str,
    require_typed_finalization: bool,
) -> ProviderRequest:
    retry_message = build_plain_text_retry_feedback(payload=tool_feedback_payload)
    if require_typed_finalization:
        retry_message = f"{retry_message}\n\n{FINALIZATION_STATUS_RETRY_GUIDANCE}"
    return build_retry_request(
        runner,
        initial_response=initial_response,
        response=response,
        follow_up_request=follow_up_request,
        retry_user_message=retry_message,
        request_user_message=build_plain_text_retry_user_message(
            base_prompt=follow_up_request.user_message
        ),
    )


def build_duplicate_tool_replan_request(
    runner,
    *,
    response: ProviderResponse,
    last_batch: ToolExecutionBatch,
    signature: str,
    deps: ExecutorDeps,
) -> ProviderRequest:
    payload = deps.tool_batch_metadata(
        batch=last_batch,
        tool_calls_count=len(response.tool_calls or []),
    ).get("tool_results", "[]")
    retry_message = build_duplicate_tool_replan_feedback(
        payload=str(payload), signature=signature
    )
    return ProviderRequest(
        user_message=build_duplicate_tool_replan_user_message(),
        system_prompt=runner.runtime.system_prompt,
        history=runner.runtime.provider_history
        + [
            ProviderHistoryMessage(
                role="assistant", content=str(getattr(response, "text", "") or "")
            ),
            ProviderHistoryMessage(role="user", content=retry_message),
        ],
        metadata={
            "identity_context": "retained",
            "duplicate_tool_replan": "true",
        },
    )


def _terminal_response(
    runner, *, state: LoopState, deps: ExecutorDeps
) -> AgentResponse | None:
    batch = state.last_batch
    if batch is None:
        return None
    if is_empty_provider_response(state.response):
        return empty_provider_response_response(
            runner,
            deps=deps,
            response=state.response,
            batch=batch,
            intent_category=state.intent_category,
            signature=state.signature,
        )
    return model_final_response(
        runner,
        deps=deps,
        initial_response=state.initial_response,
        response=state.response,
        batch=batch,
        intent_category=state.intent_category,
        signature=state.signature,
    )


async def finish_iteration(
    runner,
    *,
    state: LoopState,
    deps: ExecutorDeps,
) -> AgentResponse | None:
    batch = state.last_batch
    if batch is None:
        return None
    if state.denied:
        return blocked_tool_response(
            runner,
            deps=deps,
            response=state.response,
            batch=batch,
            intent_category=state.intent_category,
            signature=state.signature,
            security_events=state.security_events,
            tool_budget_state=state.tool_budget_state,
        )
    if looks_like_tool_call_envelope(state.response.text):
        return direct_tool_response(
            runner,
            deps=deps,
            response=state.response,
            batch=batch,
            intent_category=state.intent_category,
            signature=state.signature,
        )
    state.cumulative_tool_results.extend(list(batch.results or []))
    requires_status = requires_typed_finalization_contract_for_results(
        state.cumulative_tool_results
    )
    follow_up = build_follow_up_request(
        runner,
        deps=deps,
        response=state.response,
        batch=batch,
        require_typed_finalization=requires_status,
    )
    state.response = await runner.runtime_ops.call_provider(
        follow_up, tool_call_strategy=state.tool_call_strategy
    )
    state.response = recover_text_tool_calls(runner, response=state.response)
    if state.response.tool_calls:
        return None
    payload = deps.tool_batch_metadata(
        batch=batch,
        tool_calls_count=len(state.initial_response.tool_calls or []),
    ).get("tool_results", "[]")
    if looks_like_tool_call_envelope(state.response.text):
        state.response = await runner.runtime_ops.call_provider(
            build_plain_text_retry_request(
                runner,
                initial_response=state.initial_response,
                response=state.response,
                follow_up_request=follow_up,
                tool_feedback_payload=str(payload),
                require_typed_finalization=requires_status,
            ),
            tool_call_strategy=state.tool_call_strategy,
        )
        state.response = recover_text_tool_calls(runner, response=state.response)
    if requires_status and not bool(
        getattr(state.response, STATE_KEY_FINALIZATION_STATUS, None)
    ):
        retry_message = build_finalization_status_retry_feedback(
            payload=str(payload), guidance=FINALIZATION_STATUS_RETRY_GUIDANCE
        )
        state.response = await runner.runtime_ops.call_provider(
            build_retry_request(
                runner,
                initial_response=state.initial_response,
                response=state.response,
                follow_up_request=follow_up,
                retry_user_message=retry_message,
            ),
            tool_call_strategy=state.tool_call_strategy,
        )
        state.response = recover_text_tool_calls(runner, response=state.response)
        if not state.response.tool_calls and not bool(
            getattr(state.response, STATE_KEY_FINALIZATION_STATUS, None)
        ):
            return finalization_contract_missing_response(
                runner,
                deps=deps,
                response=state.response,
                batch=batch,
                intent_category=state.intent_category,
                signature=state.signature,
            )
    return _terminal_response(runner, state=state, deps=deps)
