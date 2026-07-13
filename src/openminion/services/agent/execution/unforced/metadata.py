import json

from openminion.base.types import AgentResponse
from openminion.modules.llm.providers.base import ProviderResponse
from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.services.agent.execution.finalization import (
    finalization_status_metadata,
    finalization_status_termination_reason,
)
from openminion.services.agent.constants import TERMINATION_REASON_LOOP_NO_PROGRESS
from openminion.services.security.policy import ToolBudgetState

from ..dependencies import ExecutorDeps
from ..response import tool_calls_payload


def _finalize(
    runner, deps: ExecutorDeps, *, text: str, metadata: dict
) -> AgentResponse:
    inbound = runner.runtime.inbound
    return deps.finalize_response(
        AgentResponse(
            text=text,
            channel=inbound.channel,
            target=inbound.target,
            metadata=metadata,
        )
    )


def _blocked_tool_response_text(batch: ToolExecutionBatch) -> str:
    for result in batch.results:
        tool_name = str(getattr(result, "tool_name", "") or "").strip()
        if not tool_name:
            continue
        data = getattr(result, "data", {}) or {}
        error_payload = data.get("error") if isinstance(data, dict) else None
        error_details = (
            error_payload.get("details", {}) if isinstance(error_payload, dict) else {}
        )
        reason_code = str(
            (error_payload.get("code") if isinstance(error_payload, dict) else "")
            or (data.get("error_code") if isinstance(data, dict) else "")
            or getattr(result, "error", "")
            or "blocked"
        ).strip()
        message = f"status=error: Tool `{tool_name}` was blocked"
        if reason_code:
            message = f"{message} ({reason_code})"
        suggested_tool = str(error_details.get("suggested_tool", "") or "").strip()
        suggested_fix = str(error_details.get("suggested_fix", "") or "").strip()
        if suggested_tool:
            message = f"{message}. Suggested tool: `{suggested_tool}`."
        if suggested_fix:
            message = f"{message} {suggested_fix}"
        return message
    return "Tool execution was blocked."


def duplicate_tool_response(
    runner,
    *,
    deps: ExecutorDeps,
    response: ProviderResponse,
    last_batch: ToolExecutionBatch | None,
    intent_category: str,
    signature: str,
) -> AgentResponse:
    metadata = {
        "model": response.model,
        "finish_reason": response.finish_reason or "tool_calls",
        "intent_category": intent_category or "none",
        "tool_loop_termination_reason": "duplicate_tool_calls",
        "tool_calls": signature,
        **(
            deps.tool_batch_metadata(
                batch=last_batch,
                tool_calls_count=len(response.tool_calls or []),
            )
            if last_batch is not None
            else {}
        ),
        **deps.identity_metadata(),
    }
    if last_batch is not None and last_batch.results:
        return _finalize(
            runner,
            deps,
            text=runner.runtime_ops._collect_batch_output(last_batch),
            metadata=metadata,
        )
    return _finalize(
        runner,
        deps,
        text="Tool execution halted due to repeated tool calls.",
        metadata=metadata,
    )


def blocked_tool_response(
    runner,
    *,
    deps: ExecutorDeps,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    intent_category: str,
    signature: str,
    security_events: list[dict[str, str]],
    tool_budget_state: ToolBudgetState | None,
) -> AgentResponse:
    metadata = {
        "model": response.model,
        "finish_reason": response.finish_reason or "tool_calls",
        "intent_category": intent_category or "none",
        "tool_loop_termination_reason": "tool_no_success",
        "tool_calls": signature,
        **deps.tool_batch_metadata(
            batch=batch,
            tool_calls_count=len(response.tool_calls or []),
        ),
        **deps.identity_metadata(),
    }
    if security_events:
        metadata["security_events"] = json.dumps(security_events, sort_keys=True)
    if tool_budget_state is not None:
        metadata["tool_budget"] = json.dumps(
            tool_budget_state.snapshot(), sort_keys=True
        )
    return _finalize(
        runner,
        deps,
        text=_blocked_tool_response_text(batch),
        metadata=metadata,
    )


def direct_tool_response(
    runner,
    *,
    deps: ExecutorDeps,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    intent_category: str,
    signature: str,
) -> AgentResponse:
    metadata = {
        "model": response.model,
        "finish_reason": "tool_direct",
        "intent_category": intent_category or "none",
        "tool_loop_termination_reason": "tool_direct",
        "tool_calls": signature,
        **deps.tool_batch_metadata(
            batch=batch,
            tool_calls_count=len(response.tool_calls or []),
        ),
        **deps.identity_metadata(),
    }
    return _finalize(
        runner,
        deps,
        text=runner.runtime_ops._collect_batch_output(batch),
        metadata=metadata,
    )


def model_final_response(
    runner,
    *,
    deps: ExecutorDeps,
    initial_response: ProviderResponse,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    intent_category: str,
    signature: str,
) -> AgentResponse:
    termination_reason = finalization_status_termination_reason(
        response,
        default="model_final",
    )
    metadata = {
        "model": response.model,
        "finish_reason": response.finish_reason or "stop",
        "intent_category": intent_category or "none",
        "tool_loop_termination_reason": termination_reason,
        "tool_calls": signature,
        **deps.tool_batch_metadata(
            batch=batch,
            tool_calls_count=len(initial_response.tool_calls or []),
        ),
        **finalization_status_metadata(response),
        **deps.identity_metadata(),
    }
    return _finalize(
        runner,
        deps,
        text=response.text,
        metadata=metadata,
    )


def finalization_contract_missing_response(
    runner,
    *,
    deps: ExecutorDeps,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    intent_category: str,
    signature: str,
) -> AgentResponse:
    metadata = {
        "model": response.model,
        "finish_reason": response.finish_reason or "stop",
        "intent_category": intent_category or "none",
        "tool_loop_termination_reason": "finalization_contract_missing",
        "tool_calls": signature,
        **deps.tool_batch_metadata(
            batch=batch,
            tool_calls_count=len(response.tool_calls or []),
        ),
        **deps.identity_metadata(),
    }
    return _finalize(
        runner,
        deps,
        text=(
            "Substantive tool-backed work ended without the required typed "
            "finalization_status contract."
        ),
        metadata=metadata,
    )


def empty_provider_response_response(
    runner,
    *,
    deps: ExecutorDeps,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    intent_category: str,
    signature: str,
) -> AgentResponse:
    """AR-14 (2026-06-06): generalize B-09's empty-response guard to the
    unforced lane. When the model returns empty text + no tool calls +
    no typed finalization_status, return a typed terminal outcome with
    `termination_reason=empty_provider_response` and the same
    `error_code=EMPTY_PROVIDER_RESPONSE` the required-lane analog emits."""
    metadata = {
        "model": response.model,
        "finish_reason": response.finish_reason or "stop",
        "intent_category": intent_category or "none",
        "tool_loop_termination_reason": "empty_provider_response",
        "error_code": "EMPTY_PROVIDER_RESPONSE",
        "tool_calls": signature,
        **deps.tool_batch_metadata(
            batch=batch,
            tool_calls_count=len(response.tool_calls or []),
        ),
        **deps.identity_metadata(),
    }
    return _finalize(
        runner,
        deps,
        text=(
            "Provider returned an empty response with no tool calls or "
            "finalization status."
        ),
        metadata=metadata,
    )


def loop_no_progress_response(
    runner,
    *,
    deps: ExecutorDeps,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    intent_category: str,
    signature: str,
    tool_name: str,
    error_code: str,
    count: int,
    threshold: int,
) -> AgentResponse:
    metadata = {
        "model": response.model,
        "finish_reason": response.finish_reason or "tool_calls",
        "intent_category": intent_category or "none",
        "tool_loop_termination_reason": TERMINATION_REASON_LOOP_NO_PROGRESS,
        "loop_no_progress_reason": "repeated_tool_failure",
        "loop_no_progress_tool_name": tool_name,
        "loop_no_progress_error_code": error_code,
        "loop_no_progress_count": str(count),
        "loop_no_progress_threshold": str(threshold),
        "tool_calls": signature,
        **deps.tool_batch_metadata(
            batch=batch,
            tool_calls_count=len(response.tool_calls or []),
        ),
        **deps.identity_metadata(),
    }
    return _finalize(
        runner,
        deps,
        text=(
            "Tool loop stopped after repeated no-progress failures for "
            f"`{tool_name}` ({error_code})."
        ),
        metadata=metadata,
    )


def max_steps_response(
    runner,
    *,
    deps: ExecutorDeps,
    response: ProviderResponse,
    last_batch: ToolExecutionBatch | None,
    intent_category: str,
) -> AgentResponse:
    metadata = {
        "model": response.model,
        "finish_reason": response.finish_reason or "tool_calls",
        "intent_category": intent_category or "none",
        "tool_loop_termination_reason": "tool_loop_max_steps",
        "tool_calls": tool_calls_payload(response.tool_calls or []),
        **(
            deps.tool_batch_metadata(
                batch=last_batch,
                tool_calls_count=len(response.tool_calls or []),
            )
            if last_batch is not None
            else {}
        ),
        **deps.identity_metadata(),
    }
    return _finalize(
        runner,
        deps,
        text="Tool loop reached max steps.",
        metadata=metadata,
    )
