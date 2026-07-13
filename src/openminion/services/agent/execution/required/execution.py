"""Required-lane tool execution, recovery, and fallback decisions."""

import json
from typing import TYPE_CHECKING, Any

from openminion.modules.tool.registry import ToolExecutionBatch

from ..dependencies import ExecutorDeps
from ..ports import ProviderResponse
from ..response import tool_calls_payload
from ..unforced.followup import build_follow_up_request, denied_tool_recovery_hint
from .metadata import build_required_outcome
from .state import RequiredLaneConfig, RequiredLaneState, _PhaseResult
from .unavailable import (
    unavailable_discovery_or_version_message,
    unavailable_discovery_retry_instruction,
)

if TYPE_CHECKING:
    from .runner import RequiredLaneRunner


def _runtime_overrides(config: RequiredLaneConfig) -> dict[str, str] | None:
    if config.allow_runtime_direct_fallback:
        return {"allow_runtime_direct": "true"}
    return None


async def _execute_response(
    runner: "RequiredLaneRunner",
    response: ProviderResponse,
    config: RequiredLaneConfig,
) -> tuple[ToolExecutionBatch, list[dict[str, str]], bool]:
    batch, security_events, denied = await runner.runtime_ops.execute_tool_calls(
        response.tool_calls,
        tool_budget_state=config.tool_budget_state,
        context_metadata_overrides=_runtime_overrides(config),
    )
    runner.runtime_ops.record_self_improvement(
        user_message=runner.runtime.user_message,
        tool_results=batch.results,
    )
    return batch, security_events, denied


async def _retry_denied_recovery(
    runner: "RequiredLaneRunner",
    *,
    state: RequiredLaneState,
    deps: ExecutorDeps,
    config: RequiredLaneConfig,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    security_events: list[dict[str, str]],
) -> _PhaseResult | tuple[ToolExecutionBatch, list[dict[str, str]], bool]:
    hint = (
        None
        if state.denied_tool_recovery_attempted
        else denied_tool_recovery_hint(batch)
    )
    if not hint:
        return batch, security_events, False
    retry_response = await runner.runtime_ops.call_provider(
        build_follow_up_request(
            runner,
            deps=deps,
            response=response,
            batch=batch,
            extra_tool_feedback=hint,
        ),
        tool_call_strategy=config.tool_call_strategy,
    )
    if not retry_response.tool_calls:
        return batch, security_events, False
    retry_batch, retry_events, retry_denied = await _execute_response(
        runner, retry_response, config
    )
    combined_batch = ToolExecutionBatch(
        results=list(batch.results) + list(retry_batch.results)
    )
    combined_events = [*security_events, *retry_events]
    if retry_batch.has_success and not retry_denied:
        return _PhaseResult(
            state_updates={
                "response": retry_response,
                "batch": combined_batch,
                "security_events": combined_events,
                "denied_tool_recovery_attempted": True,
            }
        )
    return combined_batch, combined_events, retry_denied


def _next_fallback(
    runner: "RequiredLaneRunner",
    *,
    state: RequiredLaneState,
    config: RequiredLaneConfig,
    batch: ToolExecutionBatch,
    denied: bool,
) -> _PhaseResult | None:
    if denied:
        return None
    trigger_reason = next(
        (
            reason
            for result in list(batch.results or [])
            if (reason := runner.service_port.fallback_eligibility_reason(result))
        ),
        None,
    )
    if not trigger_reason:
        return None
    attempted = set(state.attempted_tools or [])
    next_index = int(state.current_fallback_idx or 0)
    while next_index < len(config.fallback_chain):
        candidate = str(config.fallback_chain[next_index] or "").strip()
        next_index += 1
        if not candidate or candidate in attempted:
            continue
        if runner.service_port.get_spec_for_tool(candidate) is None:
            continue
        return _PhaseResult(
            action="continue",
            next_tool=candidate,
            state_updates={
                "all_attempts": [*list(state.all_attempts or []), batch],
                "current_fallback_idx": next_index,
                "capability_fallback_trigger_reason": trigger_reason,
            },
        )
    return None


async def _unavailable_retry_result(
    runner: "RequiredLaneRunner",
    *,
    state: RequiredLaneState,
    deps: ExecutorDeps,
    config: RequiredLaneConfig,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    denied: bool,
) -> _PhaseResult | None:
    message = (
        "" if denied else unavailable_discovery_or_version_message(response, batch)
    )
    if not message:
        return None
    retry_response = await runner.runtime_ops.call_provider(
        build_follow_up_request(
            runner,
            deps=deps,
            response=response,
            batch=batch,
            extra_tool_feedback=unavailable_discovery_retry_instruction(
                response, batch
            ),
        ),
        tool_call_strategy=config.tool_call_strategy,
    )
    retry_calls = list(retry_response.tool_calls or [])
    if not retry_calls:
        text = str(getattr(retry_response, "text", "") or "")
        termination_reason = "model_final"
    elif tool_calls_payload(retry_calls) == tool_calls_payload(response.tool_calls):
        text = message
        termination_reason = "tool_unavailable_final"
    else:
        return None
    return _PhaseResult(
        action="return",
        outcome=build_required_outcome(
            runner,
            deps=deps,
            text=text,
            model=str(getattr(retry_response, "model", "") or ""),
            finish_reason=str(
                getattr(retry_response, "finish_reason", "")
                or ("stop" if not retry_calls else "tool_calls")
            ),
            intent_category=config.intent_category,
            termination_reason=termination_reason,
            tool_calls_sig=tool_calls_payload(response.tool_calls),
            batch=batch,
            tool_calls_count=len(response.tool_calls or []),
            attempted_tools=list(state.attempted_tools or []),
            capability_fallback_trigger_reason=state.capability_fallback_trigger_reason,
        ),
    )


def _terminal_failure_result(
    runner: "RequiredLaneRunner",
    *,
    state: RequiredLaneState,
    deps: ExecutorDeps,
    config: RequiredLaneConfig,
    response: ProviderResponse,
    batch: ToolExecutionBatch,
    security_events: list[dict[str, str]],
) -> _PhaseResult:
    extra_metadata: dict[str, Any] = {}
    if security_events:
        extra_metadata["security_events"] = json.dumps(security_events, sort_keys=True)
    if config.tool_budget_state is not None:
        extra_metadata["tool_budget"] = json.dumps(
            config.tool_budget_state.snapshot(), sort_keys=True
        )
    return _PhaseResult(
        action="return",
        outcome=build_required_outcome(
            runner,
            deps=deps,
            text="status=error: tool execution blocked",
            model=str(getattr(response, "model", "") or ""),
            finish_reason="tool_calls",
            intent_category=config.intent_category,
            termination_reason="tool_no_success",
            tool_calls_sig=tool_calls_payload(response.tool_calls),
            batch=batch,
            tool_calls_count=len(response.tool_calls or []),
            attempted_tools=list(state.attempted_tools or []),
            capability_fallback_trigger_reason=state.capability_fallback_trigger_reason,
            extra_metadata=extra_metadata,
        ),
    )


async def phase_execute(
    runner: "RequiredLaneRunner",
    *,
    state: RequiredLaneState,
    deps: ExecutorDeps,
    config: RequiredLaneConfig,
) -> _PhaseResult:
    response = state.response
    if response is None or not response.tool_calls or state.ctx is None:
        return _PhaseResult(
            action="break",
            state_updates={"termination_reason": "required_tool_call_missing"},
        )
    batch, security_events, denied = await _execute_response(runner, response, config)
    if denied or not batch.has_success:
        recovery = await _retry_denied_recovery(
            runner,
            state=state,
            deps=deps,
            config=config,
            response=response,
            batch=batch,
            security_events=security_events,
        )
        if isinstance(recovery, _PhaseResult):
            return recovery
        batch, security_events, denied = recovery
        fallback = _next_fallback(
            runner, state=state, config=config, batch=batch, denied=denied
        )
        if fallback is not None:
            return fallback
        unavailable = await _unavailable_retry_result(
            runner,
            state=state,
            deps=deps,
            config=config,
            response=response,
            batch=batch,
            denied=denied,
        )
        if unavailable is not None:
            return unavailable
        return _terminal_failure_result(
            runner,
            state=state,
            deps=deps,
            config=config,
            response=response,
            batch=batch,
            security_events=security_events,
        )
    return _PhaseResult(
        state_updates={"batch": batch, "security_events": security_events}
    )


__all__ = ["phase_execute"]
