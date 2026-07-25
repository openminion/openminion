# ruff: noqa: F403,F405
from .common import *
from .context import ToolContext
from .invocation import ToolResult
from .schemas import HealthStatus, ToolCapabilities, ToolSchemaBundle


@runtime_checkable
class ToolMethod(Protocol):
    method_name: str
    args_schema: dict[str, Any]
    return_schema: dict[str, Any]

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult: ...


@runtime_checkable
class ToolDefinition(Protocol):
    name: str
    methods: dict[str, ToolMethod]
    capabilities: ToolCapabilities

    def schema(self) -> ToolSchemaBundle: ...


@runtime_checkable
class ToolPlugin(Protocol):
    plugin_id: str
    version: str

    def get_tools(self) -> list[ToolDefinition]: ...

    def get_config_schema(self) -> Optional[dict[str, Any]]: ...

    def validate_config(self, config: dict[str, Any]) -> None: ...

    def init(self, runtime: "ToolRuntime") -> None: ...

    def shutdown(self) -> None: ...

    def healthcheck(self) -> HealthStatus: ...
