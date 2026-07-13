from typing import Any

from .ports import RuntimeOpsPort, TurnFlowServicePort
from .required import RequiredLaneRunner
from .state import TurnRuntimeContext
from .unforced import UnforcedLaneRunner


class TurnExecutor:
    """Public facade for executor-owned turn-flow behavior."""

    def __init__(
        self,
        *,
        runtime: TurnRuntimeContext,
        service_port: TurnFlowServicePort,
        runtime_ops: RuntimeOpsPort,
        required_lane: RequiredLaneRunner,
        unforced_lane: UnforcedLaneRunner,
    ) -> None:
        self._runtime = runtime
        self._service_port = service_port
        self._runtime_ops = runtime_ops
        self._required_lane = required_lane
        self._unforced_lane = unforced_lane

    async def call_provider(self, *args: Any, **kwargs: Any) -> Any:
        return await self._runtime_ops.call_provider(*args, **kwargs)

    async def run_required_tool_lane(self, *args: Any, **kwargs: Any) -> Any:
        return await self._required_lane.run_required_tool_lane(*args, **kwargs)

    async def handle_unforced_tool_calls(self, *args: Any, **kwargs: Any) -> Any:
        return await self._unforced_lane.handle_unforced_tool_calls(
            *args,
            **kwargs,
        )


__all__ = ["TurnExecutor"]
