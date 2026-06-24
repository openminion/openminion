import json
from typing import Any

from openminion.services.security.policy import ToolBudgetState

from ..deps import ExecutorDeps
from ..ports import (
    RuntimeOpsPort,
    TurnFlowServicePort,
    resolve_runtime_context,
    resolve_runtime_ops,
    resolve_service_port,
)
from .arg_validation import phase_validate_args
from .execution_phase import phase_execute
from .metadata import (
    build_required_outcome,
    consume_required_phase_result,
    empty_required_lane_outcome,
    invalid_tool_arguments_metadata,
)
from .post_execution import phase_post_execution
from .provider_phase import phase_provider_call, phase_recover_no_tool_calls
from .state import RequiredLaneState


class RequiredLaneRunner:
    def __init__(
        self,
        *,
        service_port: TurnFlowServicePort | None = None,
        runtime: Any | None = None,
        runtime_ops: RuntimeOpsPort | None = None,
    ) -> None:
        if service_port is not None:
            self._service_port = service_port
        if runtime is not None:
            self._runtime = runtime
        if runtime_ops is not None:
            self._runtime_ops = runtime_ops

    @property
    def service_port(self) -> TurnFlowServicePort:
        return resolve_service_port(self)

    @property
    def runtime(self) -> Any:
        return resolve_runtime_context(self)

    @property
    def runtime_ops(self) -> RuntimeOpsPort:
        return resolve_runtime_ops(self)

    _invalid_tool_arguments_metadata = staticmethod(invalid_tool_arguments_metadata)

    async def _run_required_phase_sequence(
        self,
        *,
        state: RequiredLaneState,
        deps: ExecutorDeps,
        intent_category: str,
        required_tool_lane: bool,
        fallback_chain: list[str],
        capability_primary: str | None,
        tool_call_strategy: str,
        tool_budget_state: ToolBudgetState | None,
        allow_runtime_direct_fallback: bool,
    ) -> Any | None:
        tool_to_try = str(state.tool_to_try or "")
        logger = self.service_port.logger
        if logger is not None:
            logger.info("Attempting tool execution: %s", tool_to_try)
        if tool_to_try not in state.attempted_tools:
            state.attempted_tools = list(state.attempted_tools) + [tool_to_try]
        phase_calls = (
            lambda: phase_provider_call(
                self,
                state=state,
                intent_category=intent_category,
                required_tool_lane=required_tool_lane,
                tool_call_strategy=tool_call_strategy,
            ),
            lambda: phase_recover_no_tool_calls(
                self,
                state=state,
                deps=deps,
                intent_category=intent_category,
                fallback_chain=fallback_chain,
                allow_runtime_direct_fallback=allow_runtime_direct_fallback,
            ),
            lambda: phase_validate_args(
                self,
                state=state,
                deps=deps,
                intent_category=intent_category,
                fallback_chain=fallback_chain,
                allow_runtime_direct_fallback=allow_runtime_direct_fallback,
                required_tool_lane=required_tool_lane,
                tool_call_strategy=tool_call_strategy,
            ),
            lambda: phase_execute(
                self,
                state=state,
                deps=deps,
                intent_category=intent_category,
                fallback_chain=fallback_chain,
                tool_call_strategy=tool_call_strategy,
                tool_budget_state=tool_budget_state,
                allow_runtime_direct_fallback=allow_runtime_direct_fallback,
            ),
            lambda: phase_post_execution(
                self,
                state=state,
                deps=deps,
                intent_category=intent_category,
                capability_primary=capability_primary,
                fallback_chain=fallback_chain,
                tool_call_strategy=tool_call_strategy,
                tool_budget_state=tool_budget_state,
            ),
        )
        for phase_call in phase_calls:
            handled, outcome = consume_required_phase_result(
                state=state,
                result=await phase_call(),
            )
            if outcome is not None:
                return outcome
            if handled:
                return None
        state.tool_to_try = None
        return None

    async def run_required_tool_lane(
        self,
        *,
        intent_category: str,
        effective_forced_tools: list[str],
        fallback_chain: list[str],
        capability_primary: str | None,
        tool_call_strategy: str,
        tool_budget_state: ToolBudgetState | None,
        deps: ExecutorDeps,
    ) -> Any:
        selection_cfg = getattr(
            getattr(self.service_port.config, "runtime", None), "tool_selection", None
        )
        state = RequiredLaneState(
            tool_to_try=effective_forced_tools[0] if effective_forced_tools else None
        )
        allow_runtime_direct_fallback = bool(
            getattr(selection_cfg, "allow_runtime_direct_fallback", False)
        )
        required_tool_lane = bool(
            getattr(selection_cfg, "enforce_required_tool_call", True)
            and effective_forced_tools
        )

        while state.tool_to_try:
            outcome = await self._run_required_phase_sequence(
                state=state,
                deps=deps,
                intent_category=intent_category,
                required_tool_lane=required_tool_lane,
                fallback_chain=fallback_chain,
                capability_primary=capability_primary,
                tool_call_strategy=tool_call_strategy,
                tool_budget_state=tool_budget_state,
                allow_runtime_direct_fallback=allow_runtime_direct_fallback,
            )
            if outcome is not None:
                return outcome

        if required_tool_lane:
            reason = str(state.termination_reason or "required_tool_call_missing")
            final_tool = (
                state.attempted_tools[-1]
                if state.attempted_tools
                else (effective_forced_tools[0] if effective_forced_tools else "")
            )
            return build_required_outcome(
                self,
                deps=deps,
                text="Required tool call missing"
                if reason == "required_tool_call_missing"
                else "Tool execution failed",
                model="",
                finish_reason="tool_calls",
                intent_category=intent_category,
                termination_reason=reason,
                tool_calls_sig=None,
                batch=None,
                tool_calls_count=0,
                attempted_tools=list(state.attempted_tools),
                capability_fallback_trigger_reason=state.capability_fallback_trigger_reason,
                extra_metadata={
                    "capability_tool": final_tool,
                    "capability_category": intent_category or "none",
                    "capability_primary": capability_primary,
                    "capability_final_tool": final_tool,
                    "capability_fallback_trigger_reason": state.capability_fallback_trigger_reason
                    or "",
                    "capability_attempted_tools": json.dumps(
                        list(state.attempted_tools)
                    ),
                    "capability_fallback_chain": json.dumps(fallback_chain),
                    "tool_calls_count": "0",
                    "tool_execution_count": "0",
                    **self.service_port.empty_tool_resolution_metadata(),
                },
            )

        return empty_required_lane_outcome(state)


class RequiredLaneMixin(RequiredLaneRunner):
    pass


__all__ = ["RequiredLaneMixin", "RequiredLaneRunner"]
