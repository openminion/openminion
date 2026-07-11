import json
from typing import TYPE_CHECKING, Any

from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.services.security.policy import ToolBudgetState

from ..dependencies import ExecutorDeps
from .metadata import build_required_outcome
from .state import RequiredLaneState, _PhaseResult
from .unavailable import (
    unavailable_discovery_or_version_message,
    unavailable_discovery_retry_instruction,
)
from ..unforced.followup import (
    build_follow_up_request,
    denied_tool_recovery_hint,
)

if TYPE_CHECKING:
    from .runner import RequiredLaneRunner


async def phase_execute(
    runner: "RequiredLaneRunner",
    *,
    state: RequiredLaneState,
    deps: ExecutorDeps,
    intent_category: str,
    fallback_chain: list[str],
    tool_call_strategy: str,
    tool_budget_state: ToolBudgetState | None,
    allow_runtime_direct_fallback: bool,
) -> _PhaseResult:
    response = state.response
    ctx = state.ctx
    if response is None or not response.tool_calls or ctx is None:
        return _PhaseResult(
            action="break",
            state_updates={"termination_reason": "required_tool_call_missing"},
        )

    batch, security_events, denied = await runner.runtime_ops.execute_tool_calls(
        response.tool_calls,
        tool_budget_state=tool_budget_state,
        context_metadata_overrides={
            "allow_runtime_direct": "true",
        }
        if allow_runtime_direct_fallback
        else None,
    )
    runner.runtime_ops.record_self_improvement(
        user_message=runner.runtime.user_message,
        tool_results=batch.results,
    )
    if denied or not batch.has_success:
        recovery_hint = (
            None
            if state.denied_tool_recovery_attempted
            else denied_tool_recovery_hint(batch)
        )
        if recovery_hint:
            retry_response = await runner.runtime_ops.call_provider(
                build_follow_up_request(
                    runner,
                    deps=deps,
                    response=response,
                    batch=batch,
                    extra_tool_feedback=recovery_hint,
                ),
                tool_call_strategy=tool_call_strategy,
            )
            if retry_response.tool_calls:
                (
                    retry_batch,
                    retry_security_events,
                    retry_denied,
                ) = await runner.runtime_ops.execute_tool_calls(
                    retry_response.tool_calls,
                    tool_budget_state=tool_budget_state,
                    context_metadata_overrides={
                        "allow_runtime_direct": "true",
                    }
                    if allow_runtime_direct_fallback
                    else None,
                )
                runner.runtime_ops.record_self_improvement(
                    user_message=runner.runtime.user_message,
                    tool_results=retry_batch.results,
                )
                combined_batch = ToolExecutionBatch(
                    results=list(batch.results) + list(retry_batch.results)
                )
                combined_security_events = list(security_events) + list(
                    retry_security_events
                )
                if retry_batch.has_success and not retry_denied:
                    return _PhaseResult(
                        state_updates={
                            "response": retry_response,
                            "batch": combined_batch,
                            "security_events": combined_security_events,
                            "denied_tool_recovery_attempted": True,
                        }
                    )
                batch = combined_batch
                security_events = combined_security_events
                denied = retry_denied
        if not denied:
            trigger_reason = None
            for result in list(batch.results or []):
                trigger_reason = runner.service_port.fallback_eligibility_reason(result)
                if trigger_reason:
                    break
            if trigger_reason:
                attempted = set(state.attempted_tools or [])
                next_index = int(state.current_fallback_idx or 0)
                while next_index < len(fallback_chain):
                    candidate = str(fallback_chain[next_index] or "").strip()
                    next_index += 1
                    if not candidate or candidate in attempted:
                        continue
                    if runner.service_port.get_spec_for_tool(candidate) is None:
                        continue
                    return _PhaseResult(
                        action="continue",
                        next_tool=candidate,
                        state_updates={
                            "all_attempts": list(state.all_attempts or []) + [batch],
                            "current_fallback_idx": next_index,
                            "capability_fallback_trigger_reason": trigger_reason,
                        },
                    )
        unavailable_message = (
            "" if denied else unavailable_discovery_or_version_message(response, batch)
        )
        if unavailable_message:
            retry_response = await runner.runtime_ops.call_provider(
                build_follow_up_request(
                    runner,
                    deps=deps,
                    response=response,
                    batch=batch,
                    extra_tool_feedback=unavailable_discovery_retry_instruction(
                        response,
                        batch,
                    ),
                ),
                tool_call_strategy=tool_call_strategy,
            )
            if not retry_response.tool_calls:
                return _PhaseResult(
                    action="return",
                    outcome=build_required_outcome(
                        runner,
                        deps=deps,
                        text=str(getattr(retry_response, "text", "") or ""),
                        model=str(getattr(retry_response, "model", "") or ""),
                        finish_reason=str(
                            getattr(retry_response, "finish_reason", "") or "stop"
                        ),
                        intent_category=intent_category,
                        termination_reason="model_final",
                        tool_calls_sig=deps.tool_calls_payload(response.tool_calls),
                        batch=batch,
                        tool_calls_count=len(response.tool_calls or []),
                        attempted_tools=list(state.attempted_tools or []),
                        capability_fallback_trigger_reason=state.capability_fallback_trigger_reason,
                    ),
                )
            if deps.tool_calls_payload(
                retry_response.tool_calls
            ) == deps.tool_calls_payload(response.tool_calls):
                return _PhaseResult(
                    action="return",
                    outcome=build_required_outcome(
                        runner,
                        deps=deps,
                        text=unavailable_message,
                        model=str(getattr(retry_response, "model", "") or ""),
                        finish_reason=str(
                            getattr(retry_response, "finish_reason", "") or "tool_calls"
                        ),
                        intent_category=intent_category,
                        termination_reason="tool_unavailable_final",
                        tool_calls_sig=deps.tool_calls_payload(response.tool_calls),
                        batch=batch,
                        tool_calls_count=len(response.tool_calls or []),
                        attempted_tools=list(state.attempted_tools or []),
                        capability_fallback_trigger_reason=state.capability_fallback_trigger_reason,
                    ),
                )
        extra_metadata: dict[str, Any] = {}
        if security_events:
            extra_metadata["security_events"] = json.dumps(
                security_events, sort_keys=True
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
                model=str(getattr(response, "model", "") or ""),
                finish_reason="tool_calls",
                intent_category=intent_category,
                termination_reason="tool_no_success",
                tool_calls_sig=deps.tool_calls_payload(response.tool_calls),
                batch=batch,
                tool_calls_count=len(response.tool_calls or []),
                attempted_tools=list(state.attempted_tools or []),
                capability_fallback_trigger_reason=state.capability_fallback_trigger_reason,
                extra_metadata=extra_metadata,
            ),
        )
    return _PhaseResult(
        state_updates={
            "batch": batch,
            "security_events": security_events,
        }
    )


__all__ = ["phase_execute"]
