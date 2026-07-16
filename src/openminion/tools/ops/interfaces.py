from collections.abc import Callable
from typing import Protocol

from openminion.modules.tool import PLUGIN_CONTRACT_VERSION

from .contracts import (
    OperationTarget,
    TransportFacts,
    TransportReadResult,
    TransportResult,
)

OPS_PLUGIN_INTERFACE_VERSION = PLUGIN_CONTRACT_VERSION

TOOL_OPS_TARGET_LIST = "ops.target.list"
TOOL_OPS_TARGET_INSPECT = "ops.target.inspect"
TOOL_OPS_HOST_SNAPSHOT = "ops.host.snapshot"
TOOL_OPS_SERVICE_INSPECT = "ops.service.inspect"
TOOL_OPS_LOGS_QUERY = "ops.logs.query"
TOOL_OPS_NETWORK_INSPECT = "ops.network.inspect"
TOOL_OPS_COMMAND_OBSERVE = "ops.command.observe"
TOOL_OPS_JOB_INSPECT = "ops.job.inspect"
TOOL_OPS_JOB_CANCEL = "ops.job.cancel"

ALL_OPS_TOOLS = (
    TOOL_OPS_TARGET_LIST,
    TOOL_OPS_TARGET_INSPECT,
    TOOL_OPS_HOST_SNAPSHOT,
    TOOL_OPS_SERVICE_INSPECT,
    TOOL_OPS_LOGS_QUERY,
    TOOL_OPS_NETWORK_INSPECT,
    TOOL_OPS_COMMAND_OBSERVE,
    TOOL_OPS_JOB_INSPECT,
    TOOL_OPS_JOB_CANCEL,
)

OutputSink = Callable[[str, str], None]


class TargetTransport(Protocol):
    def connect(self, target: OperationTarget) -> TransportFacts: ...

    def inspect(self, target: OperationTarget) -> TransportFacts: ...

    def run(
        self,
        target: OperationTarget,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        operation_id: str = "",
        output_sink: OutputSink | None = None,
    ) -> TransportResult: ...

    def read(
        self,
        target: OperationTarget,
        path: str,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> TransportReadResult: ...

    def cancel(self, operation_id: str) -> bool: ...

    def close(self) -> None: ...
