from dataclasses import replace
from typing import TYPE_CHECKING

from ..dependencies import ExecutorDeps
from ..validators import collect_missing_required_args
from .metadata import build_required_outcome, invalid_tool_arguments_metadata
from .provider import phase_recover_no_tool_calls
from .state import RequiredLaneConfig, RequiredLaneState, _PhaseResult

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
    config: RequiredLaneConfig,
    retry_response,
    ctx,
    runtime_args_filled: bool,
) -> _PhaseResult:
    recover_result = await phase_recover_no_tool_calls(
        runner,
        state=replace(
            state,
            all_attempts=list(state.all_attempts),
            arg_retry_attempted=True,
            attempted_tools=list(state.attempted_tools),
            response=retry_response,
            runtime_args_filled=runtime_args_filled,
            ctx=ctx,
            security_events=list(state.security_events),
        ),
        config=config,
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
    config: RequiredLaneConfig,
) -> _PhaseResult:
    response = state.response
    if response is None or not response.tool_calls or runner.service_port.tools is None:
        return _PhaseResult(
            action="break",
            state_updates={"termination_reason": "required_tool_call_missing"},
        )

    request = state.request
    arg_retry_attempted = bool(state.arg_retry_attempted)

    ctx = runner.runtime_ops._build_tool_execution_context()
    if config.allow_runtime_direct_fallback and isinstance(ctx.metadata, dict):
        ctx.metadata.setdefault("allow_runtime_direct", "false")

    runtime_args_filled = False

    missing_required = collect_missing_required_args(
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
            intent_category=config.intent_category,
            response=response,
            missing_required=missing_required,
        )

    retry_response = await runner.runtime_ops.call_provider(
        request,
        tool_call_strategy=config.tool_call_strategy,
    )
    retry_missing = collect_missing_required_args(
        list(retry_response.tool_calls or []),
        spec_lookup=runner.service_port.get_spec_for_tool,
    )
    if retry_missing:
        return _invalid_args_result(
            runner,
            state=state,
            deps=deps,
            intent_category=config.intent_category,
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
        config=config,
        retry_response=retry_response,
        ctx=ctx,
        runtime_args_filled=runtime_args_filled,
    )


__all__ = ["phase_validate_args"]
