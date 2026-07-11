from typing import TYPE_CHECKING

from openminion.modules.llm.providers.base import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)

from ..dependencies import ExecutorDeps
from .state import RequiredLaneState, _PhaseResult

if TYPE_CHECKING:
    from .runner import RequiredLaneRunner


async def phase_provider_call(
    runner: "RequiredLaneRunner",
    *,
    state: RequiredLaneState,
    intent_category: str,
    required_tool_lane: bool,
    tool_call_strategy: str,
) -> _PhaseResult:
    tool_to_try = str(state.tool_to_try or "")
    spec = runner.service_port.get_spec_for_tool(tool_to_try)
    request = ProviderRequest(
        user_message=runner.runtime.user_message,
        system_prompt=runner.runtime.system_prompt,
        history=runner.runtime.provider_history,
        tools=[spec] if spec else [],
        tool_choice="required" if spec else "auto",
    )
    response = await runner.runtime_ops.call_provider(
        request, tool_call_strategy=tool_call_strategy
    )
    required_tool_retry_attempted = bool(state.required_tool_retry_attempted)
    if (
        required_tool_lane
        and not response.tool_calls
        and not required_tool_retry_attempted
        and spec is not None
    ):
        required_tool_retry_attempted = True
        retry_prompt = runner.service_port.build_required_tool_retry_prompt(
            user_message=runner.runtime.user_message,
            tool_name=tool_to_try,
            spec=spec,
        )
        retry_request = ProviderRequest(
            user_message=retry_prompt,
            system_prompt=runner.runtime.system_prompt,
            history=runner.runtime.provider_history,
            tools=[spec],
            tool_choice="required",
            metadata={
                "required_tool_retry": "true",
                "required_tool_name": tool_to_try,
                "required_category": str(intent_category or "none"),
            },
        )
        try:
            retry_response = await runner.runtime_ops.call_provider(
                retry_request,
                tool_call_strategy=tool_call_strategy,
            )
        except ProviderError as exc:
            logger = runner.service_port.logger
            if logger is not None:
                logger.warning(
                    "Required-tool repair retry failed for '%s'. Falling back to runtime path. reason=%s",
                    tool_to_try,
                    exc,
                )
            retry_response = ProviderResponse(
                text="",
                model=response.model,
                tool_calls=[],
                finish_reason="error",
            )
        if retry_response.tool_calls:
            response = retry_response
    return _PhaseResult(
        state_updates={
            "spec": spec,
            "request": request,
            "response": response,
            "required_tool_retry_attempted": required_tool_retry_attempted,
            "runtime_args_filled": False,
        }
    )


async def phase_recover_no_tool_calls(
    runner: "RequiredLaneRunner",
    *,
    state: RequiredLaneState,
    deps: ExecutorDeps,
    intent_category: str,
    fallback_chain: list[str],
    allow_runtime_direct_fallback: bool,
) -> _PhaseResult:
    response = state.response
    if response is None or response.tool_calls:
        return _PhaseResult()

    all_attempts = list(state.all_attempts or [])
    del deps, intent_category, fallback_chain, allow_runtime_direct_fallback
    return _PhaseResult(
        action="break",
        state_updates={
            "all_attempts": all_attempts,
            "termination_reason": "required_tool_call_missing",
        },
    )


__all__ = ["phase_provider_call", "phase_recover_no_tool_calls"]
