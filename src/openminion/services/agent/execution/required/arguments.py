from typing import TYPE_CHECKING

from ..dependencies import ExecutorDeps
from .metadata import build_required_outcome, invalid_tool_arguments_metadata
from .provider import phase_recover_no_tool_calls
from .state import RequiredLaneState, _PhaseResult

if TYPE_CHECKING:
    from .runner import RequiredLaneRunner


def _invalid_args_result(
    runner: "RequiredLaneRunner",
    *,
    state: RequiredLaneState,
    deps: ExecutorDeps,
    intent_category: str,
    response,
    missing_required: dict[str, list[str]],
) -> _PhaseResult:
    tool_name, missing = next(iter(missing_required.items()))
    missing_csv = ",".join(missing)
    runner.runtime_ops.record_argument_failure(
        tool_name=tool_name,
        missing_fields=missing_csv,
        user_message=runner.runtime.user_message,
    )
    return _PhaseResult(
        action="return",
        outcome=build_required_outcome(
            runner,
            deps=deps,
            text="Invalid tool arguments",
            model=str(getattr(response, "model", "") or ""),
            finish_reason="tool_calls",
            intent_category=intent_category,
            termination_reason=None,
            tool_calls_sig=None,
            batch=None,
            tool_calls_count=0,
            attempted_tools=list(state.attempted_tools or []),
            capability_fallback_trigger_reason=state.capability_fallback_trigger_reason,
            extra_metadata=invalid_tool_arguments_metadata(
                tool_name=tool_name,
                missing_fields_csv=missing_csv,
            ),
        ),
    )


async def _recover_after_arg_retry_without_calls(
    runner: "RequiredLaneRunner",
    *,
    state: RequiredLaneState,
    deps: ExecutorDeps,
    intent_category: str,
    fallback_chain: list[str],
    allow_runtime_direct_fallback: bool,
    retry_response,
    ctx,
    runtime_args_filled: bool,
) -> _PhaseResult:
    recover_result = await phase_recover_no_tool_calls(
        runner,
        state=RequiredLaneState(
            all_attempts=list(state.all_attempts),
            tool_to_try=state.tool_to_try,
            current_fallback_idx=state.current_fallback_idx,
            arg_retry_attempted=True,
            denied_tool_recovery_attempted=state.denied_tool_recovery_attempted,
            required_tool_retry_attempted=state.required_tool_retry_attempted,
            attempted_tools=list(state.attempted_tools),
            termination_reason=state.termination_reason,
            capability_fallback_trigger_reason=state.capability_fallback_trigger_reason,
            spec=state.spec,
            request=state.request,
            response=retry_response,
            runtime_args_filled=runtime_args_filled,
            ctx=ctx,
            batch=state.batch,
            security_events=list(state.security_events),
        ),
        deps=deps,
        intent_category=intent_category,
        fallback_chain=fallback_chain,
        allow_runtime_direct_fallback=allow_runtime_direct_fallback,
    )
    recover_result.state_updates = {
        "arg_retry_attempted": True,
        **recover_result.state_updates,
    }
    return recover_result


async def phase_validate_args(
    runner: "RequiredLaneRunner",
    *,
    state: RequiredLaneState,
    deps: ExecutorDeps,
    intent_category: str,
    fallback_chain: list[str],
    allow_runtime_direct_fallback: bool,
    required_tool_lane: bool,
    tool_call_strategy: str,
) -> _PhaseResult:
    response = state.response
    if response is None or not response.tool_calls or runner.service_port.tools is None:
        return _PhaseResult(
            action="break",
            state_updates={"termination_reason": "required_tool_call_missing"},
        )

    inbound = runner.runtime.inbound
    tool_to_try = str(state.tool_to_try or "")
    spec = state.spec
    request = state.request
    arg_retry_attempted = bool(state.arg_retry_attempted)

    ctx = runner.runtime_ops._build_tool_execution_context()
    if allow_runtime_direct_fallback and isinstance(ctx.metadata, dict):
        ctx.metadata.setdefault("allow_runtime_direct", "false")

    runtime_args_filled = False
    del inbound, required_tool_lane, spec, tool_to_try

    missing_required = deps.collect_missing_required_args(
        response.tool_calls,
        spec_lookup=runner.service_port.get_spec_for_tool,
    )
    if not missing_required:
        return _PhaseResult(
            state_updates={
                "ctx": ctx,
                "response": response,
                "runtime_args_filled": runtime_args_filled,
            }
        )

    if arg_retry_attempted:
        return _invalid_args_result(
            runner,
            state=state,
            deps=deps,
            intent_category=intent_category,
            response=response,
            missing_required=missing_required,
        )

    retry_response = await runner.runtime_ops.call_provider(
        request,
        tool_call_strategy=tool_call_strategy,
    )
    retry_missing = deps.collect_missing_required_args(
        list(retry_response.tool_calls or []),
        spec_lookup=runner.service_port.get_spec_for_tool,
    )
    if retry_missing:
        return _invalid_args_result(
            runner,
            state=state,
            deps=deps,
            intent_category=intent_category,
            response=retry_response,
            missing_required=retry_missing,
        )
    updates = {
        "arg_retry_attempted": True,
        "response": retry_response,
        "ctx": ctx,
        "runtime_args_filled": runtime_args_filled,
    }
    if retry_response.tool_calls:
        return _PhaseResult(state_updates=updates)

    return await _recover_after_arg_retry_without_calls(
        runner,
        state=state,
        deps=deps,
        intent_category=intent_category,
        fallback_chain=fallback_chain,
        allow_runtime_direct_fallback=allow_runtime_direct_fallback,
        retry_response=retry_response,
        ctx=ctx,
        runtime_args_filled=runtime_args_filled,
    )


__all__ = ["phase_validate_args"]
