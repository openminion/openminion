from typing import Any

from ..ports import RuntimeOpsPort, TurnFlowServicePort
from .loop import handle_unforced_tool_calls as run_unforced_tool_loop


class UnforcedLaneRunner:
    def __init__(
        self,
        *,
        service_port: TurnFlowServicePort,
        runtime: Any,
        runtime_ops: RuntimeOpsPort,
    ) -> None:
        self._service_port = service_port
        self._runtime = runtime
        self._runtime_ops = runtime_ops

    @property
    def service_port(self) -> TurnFlowServicePort:
        return self._service_port

    @property
    def runtime(self) -> Any:
        return self._runtime

    @property
    def runtime_ops(self) -> RuntimeOpsPort:
        return self._runtime_ops

    async def handle_unforced_tool_calls(self, *args: Any, **kwargs: Any) -> Any:
        return await run_unforced_tool_loop(self, *args, **kwargs)
