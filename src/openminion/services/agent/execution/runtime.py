"""Agent execution runtime coordinator."""

from typing import Any, Mapping

from openminion.modules.llm.providers.base import (
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
)
from openminion.modules.tool.base import ToolExecutionContext, ToolExecutionResult
from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.modules.tool.exposure import apply_model_exposure
from openminion.modules.policy import ToolBudgetState

from ..telemetry import trace_provider_request, trace_provider_response
from .policy import build_policy_adapter, filter_allowed_tool_calls
from .ports import TurnFlowServicePort
from .progress import execute_allowed_tool_calls, observe_tool_loop
from .resources import ExecutionResources


class ExecutorRuntime:
    def __init__(
        self,
        *,
        service_port: TurnFlowServicePort,
        runtime: Any,
    ) -> None:
        self._service_port = service_port
        self._runtime = runtime
        self._resources = ExecutionResources(service_port, runtime)

    async def call_provider(
        self, request: ProviderRequest, *, tool_call_strategy: str
    ) -> ProviderResponse:
        self._runtime.inference_steps += 1
        inference_steps = self._runtime.inference_steps
        inbound = self._runtime.inbound
        request.tool_call_strategy = tool_call_strategy
        label = f"call{inference_steps:02d}"
        request.metadata = dict(getattr(request, "metadata", {}) or {})
        inbound_meta = getattr(inbound, "metadata", {}) or {}
        session_id = str(inbound_meta.get("session_id", "") or "").strip()
        run_id = str(inbound_meta.get("run_id", "") or "").strip()
        if session_id and not request.metadata.get("session_id"):
            request.metadata["session_id"] = session_id
        if run_id and not request.metadata.get("run_id"):
            request.metadata["run_id"] = run_id
        request.metadata.update(
            {
                "turn_id": str(inbound.id),
                "inference_step": str(inference_steps),
                "trace_label": label,
            }
        )
        apply_model_exposure(request, self._service_port.tools)
        trace_args = {
            "label": label,
            "provider_name": str(
                getattr(self._service_port.provider, "name", "") or ""
            ),
            "home_root": self._service_port.home_root,
            "inbound_metadata": inbound_meta,
            "turn_id": str(inbound.id),
            "inference_step": inference_steps,
            "logger": self._service_port.logger,
        }
        trace_provider_request(provider_request=request, **trace_args)
        response = await self._service_port.generate_normalized(request)
        trace_provider_response(provider_response=response, **trace_args)
        return response

    def _build_tool_execution_context(self) -> ToolExecutionContext:
        return self._resources.build_context()

    @staticmethod
    def _collect_batch_output(batch: ToolExecutionBatch) -> str:
        outputs = [
            (result.content or str(result.data))
            if result.ok
            else f"Error: {result.error}"
            for result in batch.results or []
        ]
        return "\n".join(outputs).strip() or "Tool executed."

    async def execute_tool_calls(
        self,
        tool_calls: list[ProviderToolCall],
        *,
        tool_budget_state: ToolBudgetState | None,
        context_metadata_overrides: Mapping[str, Any] | None = None,
    ) -> tuple[ToolExecutionBatch, list[dict[str, str]], bool]:
        from openminion.modules.policy.adapters.composition import (
            SEAM_AGENT_EXECUTOR_RUNTIME,
            build_default_composition_boundary_adapter,
        )

        boundary_adapter = build_default_composition_boundary_adapter(
            seam_id=SEAM_AGENT_EXECUTOR_RUNTIME,
        )
        observe_tool_loop(self._runtime, tool_calls)
        policy_adapter = build_policy_adapter(
            self._service_port,
            self._runtime,
            tool_budget_state=tool_budget_state,
            turn_boundary_adapter=boundary_adapter,
        )
        (
            allowed_calls,
            security_events,
            denied_results,
        ) = await filter_allowed_tool_calls(
            self._service_port,
            self._runtime,
            tool_calls,
            policy_adapter=policy_adapter,
        )
        context = self._resources.build_context_with_overrides(
            context_metadata_overrides=context_metadata_overrides,
            turn_boundary_adapter=boundary_adapter,
        )
        results = execute_allowed_tool_calls(
            self._service_port,
            self._runtime,
            allowed_calls=allowed_calls,
            context=context,
        )
        results.extend(denied_results)
        return (
            ToolExecutionBatch(results=results),
            security_events,
            bool(denied_results),
        )

    def record_self_improvement(
        self, *, user_message: str, tool_results: list[ToolExecutionResult]
    ) -> None:
        improvement = self._service_port.self_improvement
        if not improvement or not improvement.enabled or not tool_results:
            return
        filtered = [
            result for result in tool_results if str(result.source or "") != "policy"
        ]
        if not filtered:
            return
        captured = improvement.capture_tool_failures(
            agent_id=self._service_port.identity_agent_id,
            user_message=user_message,
            tool_results=filtered,
        )
        metadata = self._runtime.self_improvement_metadata
        metadata["improvement_notes_captured_count"] = str(len(captured))
        if improvement.is_review_first and captured:
            metadata["improvement_review_required"] = "true"

    def record_argument_failure(
        self, *, tool_name: str, missing_fields: str, user_message: str
    ) -> None:
        improvement = self._service_port.self_improvement
        if not improvement or not improvement.enabled:
            return
        self.record_self_improvement(
            user_message=user_message,
            tool_results=[
                ToolExecutionResult(
                    tool_name=tool_name or "unknown",
                    ok=False,
                    verified=False,
                    content="",
                    error="missing_required_args",
                    data={"missing": missing_fields},
                    source="tool_arg_error",
                )
            ],
        )


__all__ = ["ExecutorRuntime"]
