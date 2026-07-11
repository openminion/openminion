from typing import Any

from .composition import build_turn_executor_components
from .state import TurnRuntimeContext


class TurnExecutor:
    """Public facade for executor-owned turn-flow behavior."""

    def __init__(
        self,
        *,
        runtime: TurnRuntimeContext,
        service: Any | None = None,
        service_port: Any | None = None,
        runtime_ops: Any | None = None,
        required_lane: Any | None = None,
        unforced_lane: Any | None = None,
    ) -> None:
        self._runtime = runtime
        if (
            service_port is None
            or runtime_ops is None
            or required_lane is None
            or unforced_lane is None
        ):
            components = build_turn_executor_components(
                service=service,
                service_port=service_port,
                runtime=runtime,
            )
            service_port = components.service_port
            runtime_ops = components.runtime_ops
            required_lane = components.required_lane
            unforced_lane = components.unforced_lane
        self._service_port = service_port
        self._runtime_ops = runtime_ops
        self._required_lane = required_lane
        self._unforced_lane = unforced_lane

    async def call_provider(self, *args: Any, **kwargs: Any) -> Any:
        return await self._runtime_ops.call_provider(*args, **kwargs)

    def _build_tool_execution_context(self, *args: Any, **kwargs: Any) -> Any:
        return self._runtime_ops._build_tool_execution_context(*args, **kwargs)

    def _collect_batch_output(self, *args: Any, **kwargs: Any) -> Any:
        return self._runtime_ops._collect_batch_output(*args, **kwargs)

    async def execute_tool_calls(self, *args: Any, **kwargs: Any) -> Any:
        return await self._runtime_ops.execute_tool_calls(*args, **kwargs)

    def record_self_improvement(self, *args: Any, **kwargs: Any) -> Any:
        return self._runtime_ops.record_self_improvement(*args, **kwargs)

    def record_argument_failure(self, *args: Any, **kwargs: Any) -> Any:
        return self._runtime_ops.record_argument_failure(*args, **kwargs)

    async def run_required_tool_lane(self, *args: Any, **kwargs: Any) -> Any:
        return await self._required_lane.run_required_tool_lane(*args, **kwargs)

    async def handle_unforced_tool_calls(self, *args: Any, **kwargs: Any) -> Any:
        return await self._unforced_lane.handle_unforced_tool_calls(
            *args,
            **kwargs,
        )


__all__ = ["TurnExecutor"]
