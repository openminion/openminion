"""MCP tool interfaces."""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .schemas import (
    MCPElicitationRequest,
    MCPElicitationResult,
    MCPCompletionResult,
    MCPListedPrompt,
    MCPListedResource,
    MCPListedResourceTemplate,
    MCPLogMessage,
    MCPResourceUpdate,
    MCPRoot,
    MCPSamplingRequest,
    MCPSamplingResult,
    MCPListedTool,
    MCPUnsupportedSchemaError,
    build_mcp_runtime_prompt_name,
    build_mcp_runtime_resource_name,
    build_mcp_runtime_resource_template_name,
    build_mcp_runtime_tool_name,
    prepare_mcp_registration_schema,
)


@runtime_checkable
class MCPFleetHandle(Protocol):
    """Runtime-facing MCP fleet interface consumed by tool registration code."""

    def has_servers(self) -> bool: ...
    def server_config(self, server_name: str) -> Any | None: ...

    def discover_tools(self, *, parallel: bool = False) -> list[MCPListedTool]: ...
    def discover_prompts(self, *, parallel: bool = False) -> list[MCPListedPrompt]: ...
    def discover_resources(
        self, *, parallel: bool = False
    ) -> list[MCPListedResource]: ...
    def discover_resource_templates(
        self, *, parallel: bool = False
    ) -> list[MCPListedResourceTemplate]: ...

    def call_tool(
        self,
        *,
        server_name: str,
        remote_name: str,
        arguments: dict[str, Any],
        progress_token: str = "",
    ) -> dict[str, Any]: ...
    def get_prompt(
        self,
        *,
        server_name: str,
        remote_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]: ...
    def read_resource(
        self,
        *,
        server_name: str,
        resource_uri: str,
    ) -> dict[str, Any]: ...

    def subscribe_resource(self, *, server_name: str, resource_uri: str) -> None: ...

    def unsubscribe_resource(self, *, server_name: str, resource_uri: str) -> None: ...

    def set_log_level(self, *, server_name: str, level: str) -> None: ...

    def complete(
        self,
        *,
        server_name: str,
        ref_type: str,
        ref_name: str,
        argument_name: str,
        argument_value: str = "",
        context_arguments: dict[str, Any] | None = None,
    ) -> MCPCompletionResult: ...

    def mcp_server_logs(self, *, limit: int = 10) -> dict[str, list[MCPLogMessage]]: ...

    def mcp_resource_updates(
        self, *, limit: int = 10
    ) -> dict[str, list[MCPResourceUpdate]]: ...

    def close_server(self, server_name: str) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class MCPSamplingHandler(Protocol):
    """Client-owned handler for server-initiated MCP sampling requests."""

    def sample(
        self,
        *,
        server_name: str,
        request: MCPSamplingRequest,
    ) -> MCPSamplingResult: ...


@runtime_checkable
class MCPElicitationHandler(Protocol):
    """Client-owned handler for server-initiated MCP elicitation requests."""

    def elicit(
        self,
        *,
        server_name: str,
        request: MCPElicitationRequest,
    ) -> MCPElicitationResult: ...


@runtime_checkable
class MCPCapabilityChangeListener(Protocol):
    """Typed listener for server-initiated MCP list_changed notifications."""

    def capability_changed(
        self,
        *,
        server_name: str,
        primitive: str,
        added: tuple[str, ...] = (),
        removed: tuple[str, ...] = (),
    ) -> None: ...


@runtime_checkable
class MCPProgressListener(Protocol):
    """Typed listener for inbound MCP progress notifications."""

    def progress_updated(
        self,
        *,
        server_name: str,
        progress_token: str,
        progress: float | None,
        message: str = "",
    ) -> None: ...


class DefaultDeclineElicitationHandler:
    """Default truthful elicitation owner for first-pass MCP breadth."""

    def elicit(
        self,
        *,
        server_name: str,
        request: MCPElicitationRequest,
    ) -> MCPElicitationResult:
        return MCPElicitationResult(action="decline")


@dataclass(frozen=True)
class MCPClientCapabilityState:
    """Typed local owner for MCP client breadth declarations."""

    roots: tuple[MCPRoot, ...] = ()
    sampling_handler: MCPSamplingHandler | None = None
    elicitation_handler: MCPElicitationHandler | None = None
    sampling_tools_supported: bool = False
    elicitation_url_supported: bool = False

    def declared_capabilities(self) -> dict[str, Any]:
        capabilities: dict[str, Any] = {}
        if self.roots:
            capabilities["roots"] = {}
        if self.sampling_handler is not None:
            sampling_capability: dict[str, Any] = {}
            if self.sampling_tools_supported:
                sampling_capability["tools"] = {}
            capabilities["sampling"] = sampling_capability
        if self.elicitation_handler is not None:
            elicitation_capability: dict[str, Any] = {"form": {}}
            if self.elicitation_url_supported:
                elicitation_capability["url"] = {}
            capabilities["elicitation"] = elicitation_capability
        return capabilities


@dataclass
class MCPToolRegistrationState:
    """Bootstrap-prepared discovery state shared by MCP registrar and plugin."""

    manager: MCPFleetHandle
    discovered_tools: tuple[MCPListedTool, ...]
    discovered_prompts: tuple[MCPListedPrompt, ...] = ()
    discovered_resources: tuple[MCPListedResource, ...] = ()
    discovered_resource_templates: tuple[MCPListedResourceTemplate, ...] = ()
    client_capability_state: MCPClientCapabilityState | None = None
    _supported_tools: tuple[MCPListedTool, ...] | None = field(
        default=None, init=False, repr=False
    )
    _passthrough_tools: tuple[str, ...] | None = field(
        default=None, init=False, repr=False
    )
    _unsupported_tools: tuple[str, ...] | None = field(
        default=None, init=False, repr=False
    )

    def _ensure_materialized(self) -> None:
        if (
            self._supported_tools is not None
            and self._passthrough_tools is not None
            and self._unsupported_tools is not None
        ):
            return

        supported_tools: list[MCPListedTool] = []
        passthrough_tools: list[str] = []
        unsupported_tools: list[str] = []
        for tool in self.discovered_tools:
            runtime_tool_name = build_mcp_runtime_tool_name(
                server_name=tool.server_name,
                remote_name=tool.remote_name,
            )
            try:
                prepared = prepare_mcp_registration_schema(tool.input_schema)
            except MCPUnsupportedSchemaError as exc:
                unsupported_tools.append(f"{runtime_tool_name}:{exc}")
                continue
            supported_tools.append(tool)
            if prepared.mode == "passthrough":
                passthrough_note = prepared.note or "passthrough"
                passthrough_tools.append(f"{runtime_tool_name}:{passthrough_note}")

        self._supported_tools = tuple(supported_tools)
        self._passthrough_tools = tuple(passthrough_tools)
        self._unsupported_tools = tuple(unsupported_tools)

    @property
    def supported_tools(self) -> tuple[MCPListedTool, ...]:
        self._ensure_materialized()
        return self._supported_tools or ()

    @property
    def unsupported_tools(self) -> tuple[str, ...]:
        self._ensure_materialized()
        return self._unsupported_tools or ()

    @property
    def passthrough_tools(self) -> tuple[str, ...]:
        self._ensure_materialized()
        return self._passthrough_tools or ()

    @property
    def supported_prompts(self) -> tuple[MCPListedPrompt, ...]:
        return tuple(self.discovered_prompts)

    @property
    def supported_resources(self) -> tuple[MCPListedResource, ...]:
        return tuple(self.discovered_resources)

    @property
    def supported_resource_templates(self) -> tuple[MCPListedResourceTemplate, ...]:
        return tuple(self.discovered_resource_templates)

    @property
    def added_runtime_tools(self) -> tuple[str, ...]:
        runtime_tools = [
            build_mcp_runtime_tool_name(
                server_name=tool.server_name,
                remote_name=tool.remote_name,
            )
            for tool in self.supported_tools
        ]
        runtime_tools.extend(
            build_mcp_runtime_prompt_name(
                server_name=prompt.server_name,
                remote_name=prompt.remote_name,
            )
            for prompt in self.supported_prompts
        )
        runtime_tools.extend(
            build_mcp_runtime_resource_name(
                server_name=resource.server_name,
                resource_uri=resource.resource_uri,
                resource_name=resource.resource_name,
            )
            for resource in self.supported_resources
        )
        runtime_tools.extend(
            build_mcp_runtime_resource_template_name(
                server_name=template.server_name,
                uri_template=template.uri_template,
                template_name=template.template_name,
            )
            for template in self.supported_resource_templates
        )
        return tuple(sorted(runtime_tools))

    @property
    def error_summary(self) -> str:
        passthrough_tools = self.passthrough_tools
        unsupported_tools = self.unsupported_tools
        parts: list[str] = []
        if passthrough_tools:
            parts.append("passthrough_mcp_tools=" + ",".join(passthrough_tools))
        if unsupported_tools:
            parts.append("unsupported_mcp_tools=" + ",".join(unsupported_tools))
        failed_servers = getattr(self.manager, "failed_servers", {}) or {}
        if failed_servers:
            formatted: list[str] = []
            for server_name, error in dict(failed_servers).items():
                reason_code = str(getattr(error, "reason_code", "") or "").strip()
                message = str(getattr(error, "message", "") or "").strip()
                token = f"{server_name}:{reason_code}"
                if message:
                    token += f":{message}"
                formatted.append(token)
            parts.append("failed_mcp_servers=" + ",".join(sorted(formatted)))
        if not parts:
            return ""
        return " ".join(parts)


def require_mcp_tool_registration_state(
    state: object | None,
) -> MCPToolRegistrationState:
    if isinstance(state, MCPToolRegistrationState):
        return state
    raise RuntimeError(
        "MCP registrar/plugin requires MCPToolRegistrationState in "
        "ToolRegisterContext.prepared_state."
    )


__all__ = [
    "DefaultDeclineElicitationHandler",
    "MCPClientCapabilityState",
    "MCPCapabilityChangeListener",
    "MCPElicitationHandler",
    "MCPFleetHandle",
    "MCPProgressListener",
    "MCPSamplingHandler",
    "MCPToolRegistrationState",
    "require_mcp_tool_registration_state",
]
