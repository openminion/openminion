"""Per-server MCP session lifecycle and normalization."""

import logging
import time
from collections import deque
from typing import Any

from openminion.base.config.mcp import MCPServerConfig

from .auth import MCPTokenStore
from .constants import (
    MCP_CLIENT_NAME,
    MCP_CLIENT_VERSION,
    MCP_COMPLETION_COMPLETE_METHOD,
    MCP_ELICITATION_COMPLETE_NOTIFICATION,
    MCP_ELICITATION_CREATE_METHOD,
    MCP_INITIALIZE_METHOD,
    MCP_INITIALIZED_NOTIFICATION,
    MCP_LOGGING_MESSAGE_NOTIFICATION,
    MCP_LOGGING_SET_LEVEL_METHOD,
    MCP_LOG_LEVELS,
    MCP_MODERN_CACHEABLE_METHODS,
    MCP_PROMPTS_GET_METHOD,
    MCP_PROMPTS_LIST_METHOD,
    MCP_RESOURCES_LIST_METHOD,
    MCP_RESOURCES_READ_METHOD,
    MCP_RESOURCES_SUBSCRIBE_METHOD,
    MCP_RESOURCES_TEMPLATES_LIST_METHOD,
    MCP_RESOURCES_UNSUBSCRIBE_METHOD,
    MCP_RESOURCES_UPDATED_NOTIFICATION,
    MCP_ROOTS_LIST_METHOD,
    MCP_SAMPLING_CREATE_MESSAGE_METHOD,
    MCP_SERVER_DISCOVER_METHOD,
    MCP_SUBSCRIPTIONS_LISTEN_METHOD,
    MCP_TASKS_CANCEL_METHOD,
    MCP_TOOLS_CALL_METHOD,
    MCP_TOOLS_LIST_METHOD,
)
from .contracts import (
    MCP_PROTOCOL_VERSION,
    MCP_SUPPORTED_PROTOCOL_VERSIONS,
    MCP_UNSUPPORTED_PROTOCOL_VERSION_ERROR,
)
from .errors import (
    MCPProtocolError,
    MCPRemoteTransportError,
    MCPServerUnavailableError,
    MCPTimeoutError,
)
from .modern import (
    MCPModernFlowError,
    MCPModernResponseCache,
    build_modern_client_meta,
    resolve_modern_result,
    select_modern_version,
)
from .results import (
    MCPCallError,
    coerce_optional_int,
    normalize_completion_result,
    normalize_prompt_result,
    normalize_resource_result,
    normalize_tool_result,
)
from .interfaces import MCPClientCapabilityState, MCPProgressListener, MCPTransport
from .risk import resolve_mcp_tool_posture
from .schemas import (
    MCPElicitationRequest,
    MCPCompletionResult,
    MCPListedPrompt,
    MCPListedResource,
    MCPListedResourceTemplate,
    MCPListedTool,
    MCPLogMessage,
    MCPResourceUpdate,
    MCPSamplingMessage,
    MCPSamplingRequest,
    MCPUnsupportedSchemaError,
    build_mcp_prompt_arguments_schema,
    build_mcp_resource_template_arguments_schema,
    extract_mcp_header_bindings,
)
from .transport import StdioMCPTransport, StreamableHTTPMCPTransport

logger = logging.getLogger(__name__)


class _SessionRequestRouter:
    def __init__(self, session: "MCPServerSession") -> None:
        self._session = session

    def handle_request(
        self, *, method: str, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        return self._session._handle_server_request(method=method, params=params)

    def handle_notification(self, *, method: str, params: dict[str, Any]) -> None:
        self._session._handle_server_notification(method=method, params=params)


class MCPServerSession:
    def __init__(
        self,
        server: MCPServerConfig,
        *,
        client_capability_state: MCPClientCapabilityState | None = None,
        capability_change_handler: Any | None = None,
        progress_listener: MCPProgressListener | None = None,
        token_store: MCPTokenStore | None = None,
    ) -> None:
        self._server = server
        self._response_cache = MCPModernResponseCache()
        self._transport = _build_transport(
            server,
            token_store=token_store,
            auth_change_handler=self._response_cache.clear,
        )
        self._initialized = False
        self._modern_protocol = False
        self._negotiated_protocol_version = MCP_PROTOCOL_VERSION
        self._server_info: dict[str, Any] = {}
        self._server_capabilities: dict[str, Any] = {}
        self._server_instructions = ""
        self._client_capability_state = (
            client_capability_state or MCPClientCapabilityState()
        )
        self._request_router = _SessionRequestRouter(self)
        self._restart_history: deque[float] = deque()
        self._restart_total = 0
        self._capability_change_handler = capability_change_handler
        self._progress_listener = progress_listener
        self._output_schemas_by_tool: dict[str, dict[str, Any]] = {}
        self._log_messages: deque[MCPLogMessage] = deque(maxlen=50)
        self._resource_updates: deque[MCPResourceUpdate] = deque(maxlen=100)
        self._log_level = ""

    @property
    def server_name(self) -> str:
        return self._server.name

    @property
    def server_config(self) -> MCPServerConfig:
        return self._server

    @property
    def restart_total(self) -> int:
        return self._restart_total

    @property
    def negotiated_protocol_version(self) -> str:
        return self._negotiated_protocol_version

    @property
    def server_info(self) -> dict[str, Any]:
        return dict(self._server_info)

    @property
    def server_capabilities(self) -> dict[str, Any]:
        return dict(self._server_capabilities)

    @property
    def server_instructions(self) -> str:
        return self._server_instructions

    def start(self) -> None:
        if self._initialized and self._transport.is_running():
            return
        self._transport.start()
        try:
            if not self._try_start_modern_protocol():
                self._start_legacy_protocol()
        except Exception:
            self._transport.close()
            self._initialized = False
            raise
        self._initialized = True

    def _try_start_modern_protocol(self) -> bool:
        params = {
            "_meta": build_modern_client_meta(
                self._client_capability_state.declared_capabilities()
            )
        }
        try:
            identity = self._response_cache_identity()
            result = self._response_cache.get(
                method=MCP_SERVER_DISCOVER_METHOD,
                params=params,
                identity=identity,
            )
            cache_miss = result is None
            if result is None:
                result = self._transport.request(
                    method=MCP_SERVER_DISCOVER_METHOD,
                    params=params,
                    timeout_seconds=self._server.startup_timeout_seconds,
                    server_request_handler=self._request_router,
                )
            selected_version = select_modern_version(result)
            if cache_miss:
                self._response_cache.store(
                    method=MCP_SERVER_DISCOVER_METHOD,
                    params=params,
                    result=result,
                    identity=identity,
                )
            self._negotiated_protocol_version = selected_version
            self._capture_server_metadata(result, modern=True)
        except MCPProtocolError as exc:
            code = exc.details.get("code")
            if self._server.transport == "stdio" and code in {
                -32601,
                MCP_UNSUPPORTED_PROTOCOL_VERSION_ERROR,
            }:
                self._transport.close()
                self._transport.start()
                return False
            raise
        except MCPRemoteTransportError as exc:
            if exc.reason_code == "mcp_http_legacy_candidate":
                return False
            raise
        except (MCPServerUnavailableError, MCPTimeoutError):
            raise
        except MCPModernFlowError as exc:
            raise MCPProtocolError(str(exc), reason_code=exc.reason_code) from exc
        self._modern_protocol = True
        return True

    def _start_legacy_protocol(self) -> None:
        result = self._transport.request(
            method=MCP_INITIALIZE_METHOD,
            params={
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": self._client_capability_state.declared_capabilities(),
                "clientInfo": {
                    "name": MCP_CLIENT_NAME,
                    "version": MCP_CLIENT_VERSION,
                },
            },
            timeout_seconds=self._server.startup_timeout_seconds,
        )
        self._negotiated_protocol_version = self._validate_negotiated_protocol_version(
            result
        )
        self._capture_server_metadata(result, modern=False)
        self._transport.notify(MCP_INITIALIZED_NOTIFICATION, {})

    def _capture_server_metadata(self, result: dict[str, Any], *, modern: bool) -> None:
        if modern:
            meta = result.get("_meta", {})
            info = (
                meta.get("io.modelcontextprotocol/serverInfo", {})
                if isinstance(meta, dict)
                else {}
            )
        else:
            info = result.get("serverInfo", {})
        self._server_info = dict(info) if isinstance(info, dict) else {}
        capabilities = result.get("capabilities", {})
        self._server_capabilities = (
            dict(capabilities) if isinstance(capabilities, dict) else {}
        )
        self._server_instructions = str(result.get("instructions", "") or "").strip()

    def list_tools(self) -> list[MCPListedTool]:
        self.start()
        if not self._supports_server_capability("tools"):
            return []
        cursor: str | None = None
        discovered: list[MCPListedTool] = []
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._request_with_recovery(
                method=MCP_TOOLS_LIST_METHOD,
                params=self._with_client_meta(params),
            )
            raw_tools = result.get("tools", [])
            if not isinstance(raw_tools, list):
                raise MCPProtocolError(
                    f"MCP server '{self.server_name}' returned a non-list tools payload."
                )
            for item in raw_tools:
                if not isinstance(item, dict):
                    continue
                remote_name = str(item.get("name", "") or "").strip()
                if not remote_name:
                    continue
                description = str(item.get("description", "") or "").strip()
                input_schema = item.get("inputSchema", {}) or {}
                if not isinstance(input_schema, dict):
                    input_schema = {}
                if self._server.transport == "streamable_http":
                    try:
                        header_bindings = extract_mcp_header_bindings(input_schema)
                    except MCPUnsupportedSchemaError as exc:
                        logger.warning(
                            "Skipping MCP tool %s from %s: %s",
                            remote_name,
                            self.server_name,
                            exc,
                        )
                        continue
                    self._transport.set_tool_header_bindings(
                        remote_name, header_bindings
                    )
                output_schema = item.get("outputSchema", {}) or {}
                if not isinstance(output_schema, dict):
                    output_schema = {}
                annotations = item.get("annotations", {}) or {}
                if not isinstance(annotations, dict):
                    annotations = {}
                self._output_schemas_by_tool[remote_name] = dict(output_schema)
                discovered.append(
                    MCPListedTool(
                        server_name=self.server_name,
                        remote_name=remote_name,
                        description=description,
                        input_schema=dict(input_schema),
                        annotations=dict(annotations),
                        posture=resolve_mcp_tool_posture(
                            server=self._server,
                            remote_name=remote_name,
                            annotations=annotations,
                        ),
                        output_schema=dict(output_schema),
                        title=str(item.get("title", "") or "").strip(),
                        icons=_coerce_icons(item.get("icons")),
                        metadata=_coerce_mapping(item.get("_meta")),
                        task_support=str(
                            _coerce_mapping(item.get("execution")).get(
                                "taskSupport", ""
                            )
                            or ""
                        ).strip(),
                    )
                )
            cursor = str(result.get("nextCursor", "") or "").strip() or None
            if cursor is None:
                break
        return discovered

    def list_prompts(self) -> list[MCPListedPrompt]:
        self.start()
        if not self._supports_server_capability("prompts"):
            return []
        cursor: str | None = None
        discovered: list[MCPListedPrompt] = []
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._request_with_recovery(
                method=MCP_PROMPTS_LIST_METHOD,
                params=self._with_client_meta(params),
            )
            raw_prompts = result.get("prompts", [])
            if not isinstance(raw_prompts, list):
                raise MCPProtocolError(
                    f"MCP server '{self.server_name}' returned a non-list prompts payload."
                )
            for item in raw_prompts:
                if not isinstance(item, dict):
                    continue
                remote_name = str(item.get("name", "") or "").strip()
                if not remote_name:
                    continue
                description = str(item.get("description", "") or "").strip()
                discovered.append(
                    MCPListedPrompt(
                        server_name=self.server_name,
                        remote_name=remote_name,
                        description=description,
                        arguments_schema=build_mcp_prompt_arguments_schema(
                            item.get("arguments", [])
                        ),
                        title=str(item.get("title", "") or "").strip(),
                        icons=_coerce_icons(item.get("icons")),
                        metadata=_coerce_mapping(item.get("_meta")),
                    )
                )
            cursor = str(result.get("nextCursor", "") or "").strip() or None
            if cursor is None:
                break
        return discovered

    def list_resources(self) -> list[MCPListedResource]:
        self.start()
        if not self._supports_server_capability("resources"):
            return []
        cursor: str | None = None
        discovered: list[MCPListedResource] = []
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._request_with_recovery(
                method=MCP_RESOURCES_LIST_METHOD,
                params=self._with_client_meta(params),
            )
            raw_resources = result.get("resources", [])
            if not isinstance(raw_resources, list):
                raise MCPProtocolError(
                    f"MCP server '{self.server_name}' returned a non-list resources payload."
                )
            for item in raw_resources:
                if not isinstance(item, dict):
                    continue
                resource_uri = str(item.get("uri", "") or "").strip()
                if not resource_uri:
                    continue
                discovered.append(
                    MCPListedResource(
                        server_name=self.server_name,
                        resource_uri=resource_uri,
                        resource_name=str(item.get("name", "") or "").strip(),
                        description=str(item.get("description", "") or "").strip(),
                        mime_type=str(item.get("mimeType", "") or "").strip(),
                        title=str(item.get("title", "") or "").strip(),
                        icons=_coerce_icons(item.get("icons")),
                        metadata=_coerce_mapping(item.get("_meta")),
                    )
                )
            cursor = str(result.get("nextCursor", "") or "").strip() or None
            if cursor is None:
                break
        return discovered

    def list_resource_templates(self) -> list[MCPListedResourceTemplate]:
        self.start()
        if not self._supports_server_capability("resources"):
            return []
        cursor: str | None = None
        discovered: list[MCPListedResourceTemplate] = []
        while True:
            params = {"cursor": cursor} if cursor else {}
            try:
                result = self._request_with_recovery(
                    method=MCP_RESOURCES_TEMPLATES_LIST_METHOD,
                    params=self._with_client_meta(params),
                )
            except MCPProtocolError as exc:
                if exc.details.get("code") == -32601:
                    return []
                raise
            raw_templates = result.get("resourceTemplates", [])
            if not isinstance(raw_templates, list):
                raise MCPProtocolError(
                    f"MCP server '{self.server_name}' returned a non-list resource templates payload."
                )
            for item in raw_templates:
                if not isinstance(item, dict):
                    continue
                uri_template = str(item.get("uriTemplate", "") or "").strip()
                if not uri_template:
                    continue
                try:
                    arguments_schema = build_mcp_resource_template_arguments_schema(
                        uri_template
                    )
                except MCPUnsupportedSchemaError as exc:
                    logger.warning(
                        "Skipping MCP resource template %s from %s: %s",
                        uri_template,
                        self.server_name,
                        exc,
                    )
                    continue
                discovered.append(
                    MCPListedResourceTemplate(
                        server_name=self.server_name,
                        uri_template=uri_template,
                        template_name=str(item.get("name", "") or "").strip(),
                        description=str(item.get("description", "") or "").strip(),
                        mime_type=str(item.get("mimeType", "") or "").strip(),
                        arguments_schema=arguments_schema,
                        title=str(item.get("title", "") or "").strip(),
                        icons=_coerce_icons(item.get("icons")),
                        metadata=_coerce_mapping(item.get("_meta")),
                    )
                )
            cursor = str(result.get("nextCursor", "") or "").strip() or None
            if cursor is None:
                break
        return discovered

    def call_tool(
        self,
        *,
        remote_name: str,
        arguments: dict[str, Any],
        progress_token: str = "",
    ) -> dict[str, Any]:
        if not self._initialized:
            self.start()
        remote_name = remote_name.strip()
        params = {
            "name": remote_name,
            "arguments": dict(arguments),
        }
        if progress_token:
            params["_meta"] = {"progressToken": progress_token.strip()}
        if self._initialized and not self._transport.is_running():
            self._restart_transport()
        try:
            result = self._request_with_recovery(
                method=MCP_TOOLS_CALL_METHOD,
                params=self._with_client_meta(params),
                retry_on_unavailable=False,
            )
        except MCPServerUnavailableError as exc:
            raise MCPCallError(
                f"MCP tool '{self.server_name}.{remote_name}' may have run before the connection was lost.",
                reason_code="mcp_outcome_uncertain",
                details={"mcp_server": self.server_name, "mcp_tool": remote_name},
            ) from exc
        return normalize_tool_result(
            server_name=self.server_name,
            remote_name=remote_name,
            result=result,
            output_schema=dict(self._output_schemas_by_tool.get(remote_name, {})),
            stderr_tail=self._transport.stderr_tail().strip(),
        )

    def get_prompt(
        self,
        *,
        remote_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._initialized:
            self.start()
        remote_name = remote_name.strip()
        result = self._request_with_recovery(
            method=MCP_PROMPTS_GET_METHOD,
            params=self._with_client_meta(
                {
                    "name": remote_name,
                    "arguments": dict(arguments),
                }
            ),
        )
        return normalize_prompt_result(
            server_name=self.server_name,
            remote_name=remote_name,
            result=result,
        )

    def read_resource(self, *, resource_uri: str) -> dict[str, Any]:
        if not self._initialized:
            self.start()
        resource_uri = resource_uri.strip()
        result = self._request_with_recovery(
            method=MCP_RESOURCES_READ_METHOD,
            params=self._with_client_meta({"uri": resource_uri}),
        )
        return normalize_resource_result(
            server_name=self.server_name,
            resource_uri=resource_uri,
            result=result,
        )

    def subscribe_resource(self, *, resource_uri: str) -> None:
        if not self._initialized:
            self.start()
        if self._modern_protocol:
            raise MCPProtocolError(
                "Modern MCP resource updates use subscriptions/listen.",
                reason_code="mcp_legacy_resource_subscription_only",
            )
        self._request_with_recovery(
            method=MCP_RESOURCES_SUBSCRIBE_METHOD,
            params=self._with_client_meta({"uri": resource_uri.strip()}),
        )

    def unsubscribe_resource(self, *, resource_uri: str) -> None:
        if not self._initialized:
            self.start()
        if self._modern_protocol:
            raise MCPProtocolError(
                "Modern MCP resource updates use subscriptions/listen.",
                reason_code="mcp_legacy_resource_subscription_only",
            )
        self._request_with_recovery(
            method=MCP_RESOURCES_UNSUBSCRIBE_METHOD,
            params=self._with_client_meta({"uri": resource_uri.strip()}),
        )

    def complete(
        self,
        *,
        ref_type: str,
        ref_name: str,
        argument_name: str,
        argument_value: str = "",
        context_arguments: dict[str, Any] | None = None,
    ) -> MCPCompletionResult:
        if not self._initialized:
            self.start()
        ref_type = ref_type.strip()
        if ref_type == "ref/prompt":
            reference = {"type": ref_type, "name": ref_name.strip()}
        elif ref_type == "ref/resource":
            reference = {"type": ref_type, "uri": ref_name.strip()}
        else:
            raise MCPProtocolError(
                f"Unsupported MCP completion reference type: {ref_type!r}.",
                reason_code="mcp_completion_ref_invalid",
            )
        result = self._request_with_recovery(
            method=MCP_COMPLETION_COMPLETE_METHOD,
            params=self._with_client_meta(
                {
                    "ref": reference,
                    "argument": {
                        "name": argument_name.strip(),
                        "value": argument_value,
                    },
                    "context": {
                        "arguments": dict(context_arguments or {}),
                    },
                }
            ),
        )
        return normalize_completion_result(result)

    def cancel(self, request_id: int) -> None:
        if self._modern_protocol and self._server.transport == "streamable_http":
            self._transport.cancel_request(request_id)
            return
        self._transport.notify(
            "notifications/cancelled",
            {
                "requestId": int(request_id),
            },
        )

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        if not self._initialized:
            self.start()
        return self._request_with_recovery(
            method=MCP_TASKS_CANCEL_METHOD,
            params=self._with_client_meta({"taskId": task_id.strip()}),
        )

    def listen(self, notifications: dict[str, Any]) -> dict[str, Any]:
        if not self._initialized:
            self.start()
        if not self._modern_protocol:
            raise MCPProtocolError(
                "subscriptions/listen requires modern MCP.",
                reason_code="mcp_modern_subscription_required",
            )
        return self._request_with_recovery(
            method=MCP_SUBSCRIPTIONS_LISTEN_METHOD,
            params=self._with_client_meta({"notifications": dict(notifications)}),
        )

    def set_log_level(self, level: str) -> None:
        normalized = level.strip().lower()
        if normalized not in MCP_LOG_LEVELS:
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' logging level is invalid.",
                reason_code="mcp_logging_level_invalid",
            )
        self.start()
        if self._modern_protocol:
            self._log_level = normalized
            return
        self._request_with_recovery(
            method=MCP_LOGGING_SET_LEVEL_METHOD,
            params={"level": normalized},
        )

    def recent_log_messages(self, limit: int = 10) -> list[MCPLogMessage]:
        return list(self._log_messages)[-max(1, int(limit)) :]

    def recent_resource_updates(self, limit: int = 10) -> list[MCPResourceUpdate]:
        return list(self._resource_updates)[-max(1, int(limit)) :]

    def close(self, *, reset_initialized: bool = True) -> None:
        self._transport.close()
        if reset_initialized:
            self._initialized = False
            self._modern_protocol = False
            self._response_cache.clear()

    def _with_client_meta(self, params: dict[str, Any]) -> dict[str, Any]:
        client_capabilities = self._client_capability_state.declared_capabilities()
        payload = dict(params)
        meta: dict[str, Any] = dict(payload.get("_meta", {}) or {})
        if self._modern_protocol:
            meta.update(build_modern_client_meta(client_capabilities))
            if self._log_level:
                meta["io.modelcontextprotocol/logLevel"] = self._log_level
            payload["_meta"] = meta
            return payload
        if client_capabilities:
            meta["io.modelcontextprotocol/clientCapabilities"] = client_capabilities
        protocol_version = str(self._negotiated_protocol_version or "").strip()
        if self._initialized and protocol_version:
            meta["io.modelcontextprotocol/protocolVersion"] = protocol_version
        if not meta:
            return payload
        payload["_meta"] = meta
        return payload

    def _request_with_recovery(
        self,
        *,
        method: str,
        params: dict[str, Any],
        retry_on_unavailable: bool = True,
    ) -> dict[str, Any]:
        cacheable = self._modern_protocol and method in MCP_MODERN_CACHEABLE_METHODS
        cached = (
            self._response_cache.get(
                method=method,
                params=params,
                identity=self._response_cache_identity(),
            )
            if cacheable
            else None
        )
        if cached is not None:
            return cached
        if self._initialized and not self._transport.is_running():
            self._restart_transport()
        try:
            result = self._transport.request(
                method=method,
                params=params,
                timeout_seconds=self._server.request_timeout_seconds,
                server_request_handler=self._request_router,
            )
        except MCPServerUnavailableError:
            if not retry_on_unavailable:
                raise
            self._restart_transport()
            result = self._transport.request(
                method=method,
                params=params,
                timeout_seconds=self._server.request_timeout_seconds,
                server_request_handler=self._request_router,
            )
        resolved = self._resolve_response(method=method, params=params, result=result)
        if cacheable:
            self._response_cache.store(
                method=method,
                params=params,
                result=resolved,
                identity=self._response_cache_identity(),
            )
        return resolved

    def _supports_server_capability(self, capability: str) -> bool:
        return capability in self._server_capabilities

    def _response_cache_identity(self) -> str:
        return self._transport.authorization_identity

    def _resolve_response(
        self,
        *,
        method: str,
        params: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._modern_protocol:
            return result
        try:
            return resolve_modern_result(
                method=method,
                params=params,
                result=result,
                request=self._request_modern,
                fulfill=lambda requested_method, requested_params: (
                    self._handle_server_request(
                        method=requested_method,
                        params=requested_params,
                    )
                ),
                timeout_seconds=self._server.request_timeout_seconds,
            )
        except MCPModernFlowError as exc:
            raise MCPProtocolError(str(exc), reason_code=exc.reason_code) from exc

    def _request_modern(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._transport.request(
            method=method,
            params=self._with_client_meta(params),
            timeout_seconds=self._server.request_timeout_seconds,
            server_request_handler=self._request_router,
        )

    def _restart_transport(self) -> None:
        if self._server.transport != "stdio":
            raise MCPServerUnavailableError(
                f"MCP server '{self.server_name}' is unavailable.",
                reason_code="mcp_server_unavailable",
            )
        self._record_restart_attempt()
        self.close(reset_initialized=True)
        self.start()

    def _record_restart_attempt(self) -> None:
        now = time.monotonic()
        while self._restart_history and (now - self._restart_history[0]) > 60.0:
            self._restart_history.popleft()
        if len(self._restart_history) >= 3:
            raise MCPServerUnavailableError(
                f"MCP server '{self.server_name}' crashed repeatedly and is unrecoverable.",
                reason_code="mcp_server_crashed_unrecoverable",
            )
        self._restart_history.append(now)
        self._restart_total += 1

    def _validate_negotiated_protocol_version(self, result: dict[str, Any]) -> str:
        negotiated = str(result.get("protocolVersion", "") or "").strip()
        if not negotiated:
            return MCP_PROTOCOL_VERSION
        if negotiated not in MCP_SUPPORTED_PROTOCOL_VERSIONS:
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' negotiated unsupported protocol version {negotiated!r}.",
                reason_code="mcp_protocol_version_unsupported",
            )
        return negotiated

    def _handle_server_request(
        self,
        *,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        if method == MCP_ROOTS_LIST_METHOD:
            return {
                "roots": [
                    {"uri": root.uri, "name": root.name}
                    for root in self._client_capability_state.roots
                ]
            }
        if method == MCP_SAMPLING_CREATE_MESSAGE_METHOD:
            sampling_handler = self._client_capability_state.sampling_handler
            if sampling_handler is None:
                raise MCPProtocolError(
                    f"MCP server '{self.server_name}' requested sampling without a declared sampling handler."
                )
            sampling_result = sampling_handler.sample(
                server_name=self.server_name,
                request=MCPSamplingRequest(
                    messages=tuple(
                        MCPSamplingMessage(
                            role=str(item.get("role", "") or "").strip(),
                            content=item.get("content"),
                        )
                        for item in (params.get("messages", []) or [])
                        if isinstance(item, dict)
                    ),
                    max_tokens=coerce_optional_int(params.get("maxTokens")),
                    system_prompt=str(params.get("systemPrompt", "") or "").strip(),
                    model_preferences=dict(params.get("modelPreferences", {}) or {}),
                    metadata=dict(params.get("metadata", {}) or {}),
                    raw_params=dict(params),
                ),
            )
            return {
                "role": sampling_result.role,
                "content": sampling_result.content,
                "model": sampling_result.model,
                "stopReason": sampling_result.stop_reason,
            }
        if method == MCP_ELICITATION_CREATE_METHOD:
            elicitation_handler = self._client_capability_state.elicitation_handler
            if elicitation_handler is None:
                raise MCPProtocolError(
                    f"MCP server '{self.server_name}' requested elicitation without a declared elicitation handler."
                )
            elicitation_result = elicitation_handler.elicit(
                server_name=self.server_name,
                request=MCPElicitationRequest(
                    mode=str(params.get("mode", "") or "").strip(),
                    message=str(params.get("message", "") or "").strip(),
                    requested_schema=dict(params.get("requestedSchema", {}) or {}),
                    url=str(params.get("url", "") or "").strip(),
                    elicitation_id=str(params.get("elicitationId", "") or "").strip(),
                    raw_params=dict(params),
                ),
            )
            payload: dict[str, Any] = {"action": elicitation_result.action}
            if elicitation_result.content is not None:
                payload["content"] = dict(elicitation_result.content)
            return payload
        if method == MCP_ELICITATION_COMPLETE_NOTIFICATION:
            return None
        raise MCPProtocolError(
            f"MCP server '{self.server_name}' requested unsupported client method {method!r}."
        )

    def _handle_server_notification(
        self,
        *,
        method: str,
        params: dict[str, Any],
    ) -> None:
        normalized = str(method or "").strip()
        if normalized in {
            "notifications/tools/list_changed",
            "notifications/resources/list_changed",
            "notifications/prompts/list_changed",
        }:
            self._response_cache.clear()
            primitive = normalized.split("/")[1]
            handler = self._capability_change_handler
            if callable(handler):
                handler(server_name=self.server_name, primitive=primitive)
            return
        if normalized == "notifications/progress":
            listener = self._progress_listener
            if listener is None:
                return
            token = str(
                params.get("progressToken")
                or params.get("token")
                or params.get("progress_token")
                or ""
            ).strip()
            progress = params.get("progress")
            numeric_progress: float | None = None
            if progress is not None:
                try:
                    numeric_progress = float(progress)
                except (TypeError, ValueError):
                    numeric_progress = None
            listener.progress_updated(
                server_name=self.server_name,
                progress_token=token,
                progress=numeric_progress,
                message=str(params.get("message", "") or "").strip(),
            )
            return
        if normalized == MCP_LOGGING_MESSAGE_NOTIFICATION:
            data = params.get("data", {})
            self._log_messages.append(
                MCPLogMessage(
                    level=str(params.get("level", "") or "").strip(),
                    message=str(params.get("message", "") or "").strip(),
                    logger=str(params.get("logger", "") or "").strip(),
                    data=dict(data) if isinstance(data, dict) else {},
                    timestamp=time.time(),
                )
            )
            return
        if normalized == MCP_RESOURCES_UPDATED_NOTIFICATION:
            self._response_cache.clear()
            uri = str(params.get("uri", "") or "").strip()
            if uri:
                self._resource_updates.append(
                    MCPResourceUpdate(
                        server_name=self.server_name,
                        uri=uri,
                        title=str(params.get("title", "") or "").strip(),
                        timestamp=time.time(),
                    )
                )
            return


def _coerce_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _coerce_icons(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, dict))


def _build_transport(
    server: MCPServerConfig,
    *,
    token_store: MCPTokenStore | None = None,
    auth_change_handler: Any | None = None,
) -> MCPTransport:
    if server.transport == "streamable_http":
        return StreamableHTTPMCPTransport(
            server,
            token_store=token_store,
            auth_change_handler=auth_change_handler,
        )
    return StdioMCPTransport(server, token_store=token_store)
