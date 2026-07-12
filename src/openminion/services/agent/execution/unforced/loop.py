from openminion.base.types import AgentResponse
from openminion.modules.llm.providers.base import (
    ProviderHistoryMessage,
    ProviderRequest,
    ProviderResponse,
)
from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.services.agent.execution.finalization import (
    FINALIZATION_STATUS_RETRY_GUIDANCE,
    requires_typed_finalization_contract_for_results,
)
from ...execution_prompts import (
    build_duplicate_tool_replan_feedback,
    build_duplicate_tool_replan_user_message,
    build_finalization_status_retry_feedback,
    build_plain_text_retry_feedback,
    build_plain_text_retry_user_message,
    build_tool_argument_retry_feedback,
)
from openminion.services.security.policy import ToolBudgetState

from ..dependencies import ExecutorDeps
from ..followup import recover_text_tool_calls

from ...constants import NO_PROGRESS_FAILURE_THRESHOLD
from .followup import build_follow_up_request
from .followup import denied_tool_recovery_hint
from .metadata import (
    blocked_tool_response,
    direct_tool_response,
    duplicate_tool_response,
    empty_provider_response_response,
    finalization_contract_missing_response,
    loop_no_progress_response,
    max_steps_response,
    model_final_response,
)
from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS

from ..validators import is_empty_provider_response


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
    response: ProviderResponse,
    last_batch: ToolExecutionBatch | None,
    intent_category: str,
    seen_signatures: set[str],
) -> tuple[str, AgentResponse | None]:
    signature = deps.tool_calls_payload(response.tool_calls)
    if signature in seen_signatures:
        return signature, duplicate_tool_response(
            runner,
            deps=deps,
            response=response,
            last_batch=last_batch,
            intent_category=intent_category,
            signature=signature,
        )
    seen_signatures.add(signature)
    return signature, None


async def _execute_batch(
    runner,
    *,
    response: ProviderResponse,
    tool_budget_state: ToolBudgetState | None,
) -> tuple[ToolExecutionBatch, object, bool]:
    execute_kwargs = {
        "tool_budget_state": tool_budget_state,
    }
    if runner.runtime_ops is not runner:
        execute_kwargs["context_metadata_overrides"] = {
            "allow_runtime_direct": "true",
        }
    batch, security_events, denied = await runner.runtime_ops.execute_tool_calls(
        response.tool_calls,
        **execute_kwargs,
    )
    runner.runtime_ops.record_self_improvement(
        user_message=runner.runtime.user_message,
        tool_results=batch.results,
    )
    return batch, security_events, denied


def _immediate_tool_result_response(
    runner,
    *,
    deps: ExecutorDeps,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    denied: bool,
    security_events,
    tool_budget_state: ToolBudgetState | None,
    intent_category: str,
    signature: str,
) -> AgentResponse | None:
    if denied:
        return blocked_tool_response(
            runner,
            deps=deps,
            response=response,
            batch=batch,
            intent_category=intent_category,
            signature=signature,
            security_events=security_events,
            tool_budget_state=tool_budget_state,
        )
    if deps.looks_like_tool_call_envelope(response.text):
        return direct_tool_response(
            runner,
            deps=deps,
            response=response,
            batch=batch,
            intent_category=intent_category,
            signature=signature,
        )
    return None


def _build_retry_request(
    runner,
    *,
    initial_response: ProviderResponse,
    response: ProviderResponse,
    follow_up_request,
    retry_user_message: str,
):
    return type(follow_up_request)(
        user_message=retry_user_message,
        system_prompt=runner.runtime.system_prompt,
        history=runner.runtime.provider_history
        + [
            ProviderHistoryMessage(
                role="assistant",
                content=str(getattr(initial_response, "text", "") or ""),
            ),
            ProviderHistoryMessage(
                role="user",
                content=follow_up_request.user_message,
            ),
            ProviderHistoryMessage(
                role="assistant",
                content=str(getattr(response, "text", "") or ""),
            ),
            ProviderHistoryMessage(
                role="user",
                content=retry_user_message,
            ),
        ],
        metadata={"identity_context": "retained"},
    )


def _build_plain_text_follow_up_retry_request(
    runner,
    *,
    initial_response: ProviderResponse,
    response: ProviderResponse,
    follow_up_request,
    tool_feedback_payload: str,
    require_typed_finalization: bool,
):
    retry_user_message = build_plain_text_retry_feedback(payload=tool_feedback_payload)
    if require_typed_finalization:
        retry_user_message = (
            f"{retry_user_message}\n\n{FINALIZATION_STATUS_RETRY_GUIDANCE}"
        )
    return type(follow_up_request)(
        user_message=(
            build_plain_text_retry_user_message(
                base_prompt=follow_up_request.user_message
            )
        ),
        system_prompt=runner.runtime.system_prompt,
        history=runner.runtime.provider_history
        + [
            ProviderHistoryMessage(
                role="assistant",
                content=str(getattr(initial_response, "text", "") or ""),
            ),
            ProviderHistoryMessage(
                role="user",
                content=follow_up_request.user_message,
            ),
            ProviderHistoryMessage(
                role="assistant",
                content=str(getattr(response, "text", "") or ""),
            ),
            ProviderHistoryMessage(
                role="user",
                content=retry_user_message,
            ),
        ],
        metadata={"identity_context": "retained"},
    )


def _build_duplicate_tool_replan_request(
    runner,
    *,
    response: ProviderResponse,
    last_batch: ToolExecutionBatch,
    signature: str,
    deps: ExecutorDeps,
):
    tool_feedback_payload = deps.tool_batch_metadata(
        batch=last_batch,
        tool_calls_count=len(response.tool_calls or []),
    ).get("tool_results", "[]")
    retry_user_message = build_duplicate_tool_replan_feedback(
        payload=str(tool_feedback_payload),
        signature=signature,
    )
    return ProviderRequest(
        user_message=build_duplicate_tool_replan_user_message(),
        system_prompt=runner.runtime.system_prompt,
        history=runner.runtime.provider_history
        + [
            ProviderHistoryMessage(
                role="assistant",
                content=str(getattr(response, "text", "") or ""),
            ),
            ProviderHistoryMessage(role="user", content=retry_user_message),
        ],
        metadata={
            "identity_context": "retained",
            "duplicate_tool_replan": "true",
        },
    )


def _tool_argument_retry_feedback(
    *, deps: ExecutorDeps, batch: ToolExecutionBatch
) -> str | None:
    if not any(deps.is_tool_argument_error(result) for result in batch.results):
        return None
    missing_fields = str(
        deps.extract_missing_argument_fields(list(batch.results)) or ""
    )
    return build_tool_argument_retry_feedback(missing_fields=missing_fields)


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


async def handle_unforced_tool_calls(
    runner,
    *,
    initial_response: ProviderResponse,
    intent_category: str,
    tool_call_strategy: str,
    tool_budget_state: ToolBudgetState | None,
    deps: ExecutorDeps,
) -> AgentResponse:
    max_steps = _max_steps_for_runner(runner)
    seen_signatures: set[str] = set()
    denied_recovery_attempted = False
    response = initial_response
    last_batch: ToolExecutionBatch | None = None
    cumulative_tool_results: list[object] = []
    duplicate_replan_attempted = False
    tool_arg_retry_attempted = False
    failure_counts: dict[tuple[str, str], int] = {}

    for _ in range(max_steps):
        if not response.tool_calls:
            break
        signature, duplicate_response = _duplicate_signature_response(
            runner,
            deps=deps,
            response=response,
            last_batch=last_batch,
            intent_category=intent_category,
            seen_signatures=seen_signatures,
        )
        if duplicate_response is not None:
            if (
                not duplicate_replan_attempted
                and last_batch is not None
                and last_batch.results
            ):
                duplicate_replan_attempted = True
                response = await runner.runtime_ops.call_provider(
                    _build_duplicate_tool_replan_request(
                        runner,
                        response=response,
                        last_batch=last_batch,
                        signature=signature,
                        deps=deps,
                    ),
                    tool_call_strategy=tool_call_strategy,
                )
                response = recover_text_tool_calls(runner, response=response)
                if response.tool_calls:
                    continue
                if is_empty_provider_response(response):
                    return empty_provider_response_response(
                        runner,
                        deps=deps,
                        response=response,
                        batch=last_batch,
                        intent_category=intent_category,
                        signature=signature,
                    )
                return model_final_response(
                    runner,
                    deps=deps,
                    initial_response=initial_response,
                    response=response,
                    batch=last_batch,
                    intent_category=intent_category,
                    signature=signature,
                )
            return duplicate_response

        batch, security_events, denied = await _execute_batch(
            runner,
            response=response,
            tool_budget_state=tool_budget_state,
        )
        last_batch = batch
        failure_signature = (
            _failure_signature(batch) if denied or not batch.has_success else None
        )
        failure_count = 0
        if failure_signature is not None:
            failure_count = failure_counts.get(failure_signature, 0) + 1
            failure_counts[failure_signature] = failure_count
        if denied or not batch.has_success:
            argument_retry_feedback = (
                None
                if denied or tool_arg_retry_attempted
                else _tool_argument_retry_feedback(deps=deps, batch=batch)
            )
            if argument_retry_feedback:
                tool_arg_retry_attempted = True
                follow_up_request = build_follow_up_request(
                    runner,
                    deps=deps,
                    response=response,
                    batch=batch,
                    extra_tool_feedback=argument_retry_feedback,
                )
                response = await runner.runtime_ops.call_provider(
                    follow_up_request,
                    tool_call_strategy=tool_call_strategy,
                )
                response = recover_text_tool_calls(runner, response=response)
                if response.tool_calls:
                    continue
            recovery_hint = (
                None if denied_recovery_attempted else denied_tool_recovery_hint(batch)
            )
            if recovery_hint:
                denied_recovery_attempted = True
                follow_up_request = build_follow_up_request(
                    runner,
                    deps=deps,
                    response=response,
                    batch=batch,
                    extra_tool_feedback=recovery_hint,
                )
                response = await runner.runtime_ops.call_provider(
                    follow_up_request,
                    tool_call_strategy=tool_call_strategy,
                )
                response = recover_text_tool_calls(runner, response=response)
                if response.tool_calls:
                    continue
            if (
                failure_signature is not None
                and failure_count >= NO_PROGRESS_FAILURE_THRESHOLD
            ):
                tool_name, error_code = failure_signature
                return loop_no_progress_response(
                    runner,
                    deps=deps,
                    response=response,
                    batch=batch,
                    intent_category=intent_category,
                    signature=signature,
                    tool_name=tool_name,
                    error_code=error_code,
                    count=failure_count,
                    threshold=NO_PROGRESS_FAILURE_THRESHOLD,
                )
        immediate_response = _immediate_tool_result_response(
            runner,
            deps=deps,
            response=response,
            batch=batch,
            denied=denied,
            security_events=security_events,
            tool_budget_state=tool_budget_state,
            intent_category=intent_category,
            signature=signature,
        )
        if immediate_response is not None:
            return immediate_response

        cumulative_tool_results.extend(list(batch.results or []))
        requires_finalization_status = requires_typed_finalization_contract_for_results(
            cumulative_tool_results
        )
        follow_up_request = build_follow_up_request(
            runner,
            deps=deps,
            response=response,
            batch=batch,
            require_typed_finalization=requires_finalization_status,
        )
        response = await runner.runtime_ops.call_provider(
            follow_up_request,
            tool_call_strategy=tool_call_strategy,
        )
        response = recover_text_tool_calls(runner, response=response)
        if not response.tool_calls:
            tool_feedback_payload = deps.tool_batch_metadata(
                batch=batch,
                tool_calls_count=len(initial_response.tool_calls or []),
            ).get("tool_results", "[]")
            if deps.looks_like_tool_call_envelope(response.text):
                response = await runner.runtime_ops.call_provider(
                    _build_plain_text_follow_up_retry_request(
                        runner,
                        initial_response=initial_response,
                        response=response,
                        follow_up_request=follow_up_request,
                        tool_feedback_payload=tool_feedback_payload,
                        require_typed_finalization=requires_finalization_status,
                    ),
                    tool_call_strategy=tool_call_strategy,
                )
                response = recover_text_tool_calls(runner, response=response)
            if requires_finalization_status and not bool(
                getattr(response, STATE_KEY_FINALIZATION_STATUS, None)
            ):
                retry_user_message = build_finalization_status_retry_feedback(
                    payload=str(tool_feedback_payload),
                    guidance=FINALIZATION_STATUS_RETRY_GUIDANCE,
                )
                response = await runner.runtime_ops.call_provider(
                    _build_retry_request(
                        runner,
                        initial_response=initial_response,
                        response=response,
                        follow_up_request=follow_up_request,
                        retry_user_message=retry_user_message,
                    ),
                    tool_call_strategy=tool_call_strategy,
                )
                response = recover_text_tool_calls(runner, response=response)
                if not response.tool_calls and not bool(
                    getattr(response, STATE_KEY_FINALIZATION_STATUS, None)
                ):
                    return finalization_contract_missing_response(
                        runner,
                        deps=deps,
                        response=response,
                        batch=batch,
                        intent_category=intent_category,
                        signature=signature,
                    )
            if is_empty_provider_response(response):
                return empty_provider_response_response(
                    runner,
                    deps=deps,
                    response=response,
                    batch=batch,
                    intent_category=intent_category,
                    signature=signature,
                )
            return model_final_response(
                runner,
                deps=deps,
                initial_response=initial_response,
                response=response,
                batch=batch,
                intent_category=intent_category,
                signature=signature,
            )

    return max_steps_response(
        runner,
        deps=deps,
        response=response,
        last_batch=last_batch,
        intent_category=intent_category,
    )


__all__ = ["handle_unforced_tool_calls"]
