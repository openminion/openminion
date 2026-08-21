"""Published-tool registry shape for the OpenMinion MCP server."""

import fnmatch
import json
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

from openminion.base.config.mcp import MCPPublishConfig, coerce_mcp_publish_config
from openminion.base.version import OPENMINION_VERSION
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.contracts import ProviderToolCall
from openminion.modules.tool.registry import ToolRegistry

from ..constants import (
    MCP_INITIALIZE_METHOD,
    MCP_INITIALIZED_NOTIFICATION,
    MCP_SERVER_DISCOVER_METHOD,
    MCP_TOOLS_CALL_METHOD,
    MCP_TOOLS_LIST_METHOD,
)
from ..contracts import (
    MCP_MODERN_PROTOCOL_VERSION,
    MCP_PROTOCOL_VERSION,
    MCP_SUPPORTED_PROTOCOL_VERSIONS,
)


class MCPServerError(RuntimeError):
    """Raised by :func:`invoke_published_tool` for unknown / failed tools."""


@dataclass
class PublishedTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]
    tags: list[str] = field(default_factory=list)
    runtime_tool_name: str = ""
    dangerous: bool = False
    min_scope: str = "READ_ONLY"


def render_tools_list_payload(
    tools: list[PublishedTool],
) -> dict[str, Any]:
    """Produce the MCP ``tools/list`` response shape.

    Spec: each tool entry has ``name``, ``description``, ``inputSchema``.
    """

    return {
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            for tool in tools
        ]
    }


def invoke_published_tool(
    tools: list[PublishedTool],
    *,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch an MCP ``tools/call`` request to the matching tool."""

    by_name = {tool.name: tool for tool in tools}
    if name not in by_name:
        raise MCPServerError(f"unknown MCP tool: {name!r}")
    tool = by_name[name]
    try:
        result = tool.handler(arguments)
    except Exception as exc:  # noqa: BLE001 — surface as MCP error
        raise MCPServerError(f"openminion MCP tool {name!r} failed: {exc!r}") from exc
    return {
        "content": [
            {
                "type": "text",
                "text": _coerce_text(result),
            }
        ],
        "isError": False,
    }


def handle_published_mcp_request(
    tools: list[PublishedTool],
    request: dict[str, Any],
) -> dict[str, Any] | None:
    """Handle the JSON-RPC methods needed by stdio/HTTP MCP adapters.

    Transport adapters can use this function for both stdio and streamable HTTP
    surfaces so publication dispatch stays in one place.
    """

    method = str(request.get("method") or "").strip()
    request_id = request.get("id")
    if not method:
        return _jsonrpc_error(request_id, code=-32600, message="method is required")
    if method == MCP_SERVER_DISCOVER_METHOD:
        return _jsonrpc_result(
            request_id,
            {
                "supportedVersions": list(MCP_SUPPORTED_PROTOCOL_VERSIONS),
                "serverInfo": {"name": "openminion", "version": OPENMINION_VERSION},
                "capabilities": {"tools": {}},
                "resultType": "complete",
            },
        )
    if method == MCP_INITIALIZE_METHOD:
        return _jsonrpc_result(
            request_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "serverInfo": {"name": "openminion", "version": OPENMINION_VERSION},
                "capabilities": {"tools": {}},
            },
        )
    if method == MCP_INITIALIZED_NOTIFICATION:
        return None
    modern = _is_modern_request(request)
    if method == MCP_TOOLS_LIST_METHOD:
        result = render_tools_list_payload(
            sorted(tools, key=lambda tool: tool.name) if modern else tools
        )
        if modern:
            result.update(
                {
                    "resultType": "complete",
                    "ttlMs": 0,
                    "cacheScope": "private",
                }
            )
        return _jsonrpc_result(request_id, result)
    if method == MCP_TOOLS_CALL_METHOD:
        params = request.get("params")
        if not isinstance(params, dict):
            return _jsonrpc_error(
                request_id, code=-32602, message="tools/call params must be an object"
            )
        name = str(params.get("name") or "").strip()
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _jsonrpc_error(
                request_id,
                code=-32602,
                message="tools/call arguments must be an object",
            )
        try:
            result = invoke_published_tool(tools, name=name, arguments=arguments)
            if modern:
                result["resultType"] = "complete"
            return _jsonrpc_result(request_id, result)
        except MCPServerError as exc:
            return _jsonrpc_error(request_id, code=-32000, message=str(exc))
    return _jsonrpc_error(
        request_id,
        code=-32601,
        message=f"unsupported MCP server method: {method}",
    )


def _is_modern_request(request: dict[str, Any]) -> bool:
    params = request.get("params", {})
    if not isinstance(params, dict):
        return False
    meta = params.get("_meta", {})
    if not isinstance(meta, dict):
        return False
    return (
        str(meta.get("io.modelcontextprotocol/protocolVersion", "") or "").strip()
        == MCP_MODERN_PROTOCOL_VERSION
    )


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    return str(value)


def _jsonrpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, *, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def build_runtime_published_tools(
    runtime: Any,
    *,
    publish_config: MCPPublishConfig | dict[str, Any] | None = None,
) -> list[PublishedTool]:
    """Build an opt-in MCP publication catalog backed by ``runtime.tools``.

    This intentionally publishes nothing unless ``runtime.mcp_publish.enabled``
    (or an explicit enabled ``publish_config``) is true. The handlers execute
    through the normal ToolRegistry path, preserving policy, validation,
    telemetry, and tool-result normalization.
    """

    config = _resolve_publish_config(runtime, publish_config)
    if not config.enabled:
        return []
    registry: ToolRegistry = runtime.tools
    runtime_tools = registry.list()
    published: list[PublishedTool] = []
    for runtime_name, tool in runtime_tools.items():
        if not _tool_allowed_by_publish_config(runtime_name, config):
            continue
        provider_spec = registry.provider_spec_for_name(runtime_name)
        if provider_spec is None:
            raise MCPServerError(
                f"OpenMinion tool {runtime_name!r} has no provider schema."
            )
        published_name = _published_tool_name(runtime_name, prefix=config.name_prefix)
        dangerous, min_scope = _tool_posture(registry, runtime_name)
        published.append(
            PublishedTool(
                name=published_name,
                description=str(provider_spec.description or runtime_name),
                input_schema=dict(provider_spec.parameters),
                handler=_runtime_tool_handler(
                    registry=registry,
                    authored_tools=runtime.authored_tools,
                    sandbox_runner=runtime.sandbox_runner,
                    runtime_tool_name=runtime_name,
                    published_name=published_name,
                ),
                tags=["openminion", "runtime"],
                runtime_tool_name=runtime_name,
                dangerous=dangerous,
                min_scope=min_scope,
            )
        )
    return published


def _resolve_publish_config(
    runtime: Any,
    publish_config: MCPPublishConfig | dict[str, Any] | None,
) -> MCPPublishConfig:
    if publish_config is not None:
        return coerce_mcp_publish_config(publish_config)
    return coerce_mcp_publish_config(runtime.config.runtime.mcp_publish)


def _tool_allowed_by_publish_config(
    runtime_name: str,
    config: MCPPublishConfig,
) -> bool:
    if config.include_tools and not any(
        fnmatch.fnmatch(runtime_name, pattern) for pattern in config.include_tools
    ):
        return False
    if config.exclude_tools and any(
        fnmatch.fnmatch(runtime_name, pattern) for pattern in config.exclude_tools
    ):
        return False
    return True


def _published_tool_name(runtime_name: str, *, prefix: str) -> str:
    safe = rejoin_tool_name(runtime_name)
    return f"{prefix}{safe}"


def rejoin_tool_name(runtime_name: str) -> str:
    return runtime_name.strip().replace(" ", "_")


def _runtime_tool_handler(
    *,
    registry: ToolRegistry,
    authored_tools: Any | None,
    sandbox_runner: Any | None,
    runtime_tool_name: str,
    published_name: str,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def _handler(arguments: dict[str, Any]) -> dict[str, Any]:
        call_arguments = dict(arguments)
        session_id = str(call_arguments.pop("_session_id", "") or "").strip()
        context = ToolExecutionContext(
            channel="mcp-server",
            target="external-mcp-client",
            session_id=session_id,
            authored_tools_api=authored_tools,
            sandbox_runner=sandbox_runner,
            metadata={
                "origin": "mcp.server.publish",
                "published_tool": published_name,
                "runtime_tool": runtime_tool_name,
                "trace_id": f"mcp-publish-{uuid4().hex}",
            },
        )
        batch = registry.execute_calls(
            [
                ProviderToolCall(
                    name=runtime_tool_name,
                    arguments=call_arguments,
                    id=str(context.metadata["trace_id"]),
                    source="mcp_server_publish",
                )
            ],
            context=context,
        )
        results = batch.results
        if not results:
            raise MCPServerError(
                f"OpenMinion tool {runtime_tool_name!r} returned no result."
            )
        result = results[0]
        if not result.ok:
            raise MCPServerError(result.error or "published tool call failed")
        return {
            "tool": runtime_tool_name,
            "content": result.content,
            "verified": result.verified,
            "data": dict(result.data),
            "source": result.source,
        }

    return _handler


def _tool_posture(registry: ToolRegistry, runtime_name: str) -> tuple[bool, str]:
    policy = registry.policy_for(runtime_name)
    dangerous = policy.risk == "high"
    scope_tokens = set(policy.required_scopes_all)
    min_scope = "POWER_USER" if "tool.execute.elevated" in scope_tokens else "READ_ONLY"
    return dangerous, min_scope
