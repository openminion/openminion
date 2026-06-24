from typing import Any

from ..ports import (
    RuntimeOpsPort,
    TurnFlowServicePort,
    resolve_runtime_context,
    resolve_runtime_ops,
    resolve_service_port,
)
from .loop import handle_unforced_tool_calls as run_unforced_tool_loop


class UnforcedLaneRunner:
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

    async def handle_unforced_tool_calls(self, *args: Any, **kwargs: Any) -> Any:
        return await run_unforced_tool_loop(self, *args, **kwargs)


class UnforcedLaneMixin(UnforcedLaneRunner):
    pass


__all__ = ["UnforcedLaneMixin", "UnforcedLaneRunner"]
