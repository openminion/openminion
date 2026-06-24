"""Per-server MCP session lifecycle and normalization."""

import fnmatch
import json
import time
from collections import deque
from typing import Any

from openminion.base.config.mcp import MCPServerConfig

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
    MCP_TOOLS_CALL_METHOD,
    MCP_TOOLS_LIST_METHOD,
)
from .contracts import (
    MCP_PROTOCOL_VERSION,
    MCP_PROTOCOL_VERSION_FLOOR,
    protocol_version_tuple,
)
from .interfaces import MCPClientCapabilityState, MCPProgressListener
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
    MCPToolPosture,
    build_mcp_runtime_tool_name,
    build_mcp_resource_template_arguments_schema,
    validate_mcp_arguments,
)
from .transport import (
    MCPProtocolError,
    MCPServerUnavailableError,
    StreamableHTTPMCPTransport,
    StdioMCPTransport,
)


_MCP_SCOPE_ORDER: dict[str, int] = {
    "READ_ONLY": 0,
    "WRITE_SAFE": 1,
    "POWER_USER": 2,
    "UI_AUTOMATION": 3,
}


class MCPManagerError(RuntimeError):
    """Base MCP manager error."""


class MCPCallError(MCPManagerError):
    """Raised when an MCP tool call returns an error envelope."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "mcp_upstream_error",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = str(reason_code or "").strip()
        self.details = dict(details or {})


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
    ) -> None:
        self._server = server
        self._transport = _build_transport(server)
        self._initialized = False
        self._negotiated_protocol_version = MCP_PROTOCOL_VERSION
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

    @property
    def server_name(self) -> str:
        return self._server.name

    @property
    def negotiated_protocol_version(self) -> str:
        return self._negotiated_protocol_version

    def start(self) -> None:
        if self._initialized and self._transport.is_running():
            return
        self._transport.start()
        try:
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
            negotiated_version = self._validate_negotiated_protocol_version(result)
            self._negotiated_protocol_version = negotiated_version
            self._transport.notify(MCP_INITIALIZED_NOTIFICATION, {})
        except Exception:
            self._transport.close()
            self._initialized = False
            raise
        self._initialized = True

    def list_tools(self) -> list[MCPListedTool]:
        self.start()
        cursor: str | None = None
        discovered: list[MCPListedTool] = []
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._transport.request(
                method=MCP_TOOLS_LIST_METHOD,
                params=self._with_client_meta(params),
                timeout_seconds=self._server.request_timeout_seconds,
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
                        posture=_resolve_mcp_tool_posture(
                            server=self._server,
                            remote_name=remote_name,
                            annotations=annotations,
                        ),
                        output_schema=dict(output_schema),
                    )
                )
            cursor = str(result.get("nextCursor", "") or "").strip() or None
            if cursor is None:
                break
        return discovered

    def list_prompts(self) -> list[MCPListedPrompt]:
        self.start()
        cursor: str | None = None
        discovered: list[MCPListedPrompt] = []
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._transport.request(
                method=MCP_PROMPTS_LIST_METHOD,
                params=self._with_client_meta(params),
                timeout_seconds=self._server.request_timeout_seconds,
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
                        arguments_schema=_build_prompt_arguments_schema(
                            item.get("arguments", [])
                        ),
                    )
                )
            cursor = str(result.get("nextCursor", "") or "").strip() or None
            if cursor is None:
                break
        return discovered

    def list_resources(self) -> list[MCPListedResource]:
        self.start()
        cursor: str | None = None
        discovered: list[MCPListedResource] = []
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._transport.request(
                method=MCP_RESOURCES_LIST_METHOD,
                params=self._with_client_meta(params),
                timeout_seconds=self._server.request_timeout_seconds,
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
                    )
                )
            cursor = str(result.get("nextCursor", "") or "").strip() or None
            if cursor is None:
                break
        return discovered

    def list_resource_templates(self) -> list[MCPListedResourceTemplate]:
        self.start()
        cursor: str | None = None
        discovered: list[MCPListedResourceTemplate] = []
        while True:
            params = {"cursor": cursor} if cursor else {}
            try:
                result = self._transport.request(
                    method=MCP_RESOURCES_TEMPLATES_LIST_METHOD,
                    params=self._with_client_meta(params),
                    timeout_seconds=self._server.request_timeout_seconds,
                )
            except MCPProtocolError:
                return []
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
                discovered.append(
                    MCPListedResourceTemplate(
                        server_name=self.server_name,
                        uri_template=uri_template,
                        template_name=str(item.get("name", "") or "").strip(),
                        description=str(item.get("description", "") or "").strip(),
                        mime_type=str(item.get("mimeType", "") or "").strip(),
                        arguments_schema=build_mcp_resource_template_arguments_schema(
                            uri_template
                        ),
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
        params = {
            "name": str(remote_name or "").strip(),
            "arguments": dict(arguments),
        }
        if progress_token:
            params["_meta"] = {"progressToken": str(progress_token).strip()}
        result = self._request_with_recovery(
            method=MCP_TOOLS_CALL_METHOD,
            params=self._with_client_meta(params),
        )
        return self._normalize_call_result(
            remote_name=str(remote_name or "").strip(),
            result=result,
        )

    def get_prompt(
        self,
        *,
        remote_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._initialized:
            self.start()
        result = self._request_with_recovery(
            method=MCP_PROMPTS_GET_METHOD,
            params=self._with_client_meta(
                {
                    "name": str(remote_name or "").strip(),
                    "arguments": dict(arguments),
                }
            ),
        )
        return self._normalize_prompt_result(
            remote_name=str(remote_name or "").strip(),
            result=result,
        )

    def read_resource(self, *, resource_uri: str) -> dict[str, Any]:
        if not self._initialized:
            self.start()
        result = self._request_with_recovery(
            method=MCP_RESOURCES_READ_METHOD,
            params=self._with_client_meta({"uri": str(resource_uri or "").strip()}),
        )
        return self._normalize_resource_result(
            resource_uri=str(resource_uri or "").strip(),
            result=result,
        )

    def subscribe_resource(self, *, resource_uri: str) -> None:
        if not self._initialized:
            self.start()
        self._request_with_recovery(
            method=MCP_RESOURCES_SUBSCRIBE_METHOD,
            params=self._with_client_meta({"uri": str(resource_uri or "").strip()}),
        )

    def unsubscribe_resource(self, *, resource_uri: str) -> None:
        if not self._initialized:
            self.start()
        self._request_with_recovery(
            method=MCP_RESOURCES_UNSUBSCRIBE_METHOD,
            params=self._with_client_meta({"uri": str(resource_uri or "").strip()}),
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
        result = self._request_with_recovery(
            method=MCP_COMPLETION_COMPLETE_METHOD,
            params=self._with_client_meta(
                {
                    "ref": {
                        "type": str(ref_type or "").strip(),
                        "name": str(ref_name or "").strip(),
                    },
                    "argument": {
                        "name": str(argument_name or "").strip(),
                        "value": str(argument_value or ""),
                    },
                    "context": {
                        "arguments": dict(context_arguments or {}),
                    },
                }
            ),
        )
        return _normalize_completion_result(result)

    def cancel(self, request_id: int) -> None:
        self._transport.notify(
            "notifications/cancelled",
            {
                "requestId": int(request_id),
            },
        )

    def set_log_level(self, level: str) -> None:
        normalized = str(level or "").strip().lower()
        if not normalized:
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' logging level is required.",
                reason_code="mcp_logging_level_required",
            )
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

    def _with_client_meta(self, params: dict[str, Any]) -> dict[str, Any]:
        client_capabilities = self._client_capability_state.declared_capabilities()
        payload = dict(params)
        meta: dict[str, Any] = dict(payload.get("_meta", {}) or {})
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
    ) -> dict[str, Any]:
        if self._initialized and not self._transport.is_running():
            return self._restart_and_retry(method=method, params=params)
        try:
            return self._transport.request(
                method=method,
                params=params,
                timeout_seconds=self._server.request_timeout_seconds,
                server_request_handler=self._request_router,
            )
        except MCPServerUnavailableError:
            return self._restart_and_retry(method=method, params=params)

    def _restart_and_retry(
        self,
        *,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        if self._server.transport != "stdio":
            raise MCPServerUnavailableError(
                f"MCP server '{self.server_name}' is unavailable.",
                reason_code="mcp_server_unavailable",
            )
        self._record_restart_attempt()
        self.close(reset_initialized=True)
        self.start()
        return self._transport.request(
            method=method,
            params=params,
            timeout_seconds=self._server.request_timeout_seconds,
            server_request_handler=self._request_router,
        )

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
        try:
            negotiated_tuple = protocol_version_tuple(negotiated)
            min_tuple = protocol_version_tuple(MCP_PROTOCOL_VERSION_FLOOR)
        except ValueError as exc:
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' returned unsupported protocol version {negotiated!r}.",
                reason_code="mcp_protocol_version_invalid",
            ) from exc
        if negotiated_tuple < min_tuple:
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' negotiated unsupported protocol version {negotiated!r}.",
                reason_code="mcp_protocol_version_too_old",
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
            handler = self._client_capability_state.sampling_handler
            if handler is None:
                raise MCPProtocolError(
                    f"MCP server '{self.server_name}' requested sampling without a declared sampling handler."
                )
            result = handler.sample(
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
                    max_tokens=_coerce_optional_int(params.get("maxTokens")),
                    system_prompt=str(params.get("systemPrompt", "") or "").strip(),
                    model_preferences=dict(params.get("modelPreferences", {}) or {}),
                    metadata=dict(params.get("metadata", {}) or {}),
                    raw_params=dict(params),
                ),
            )
            return {
                "role": result.role,
                "content": result.content,
                "model": result.model,
                "stopReason": result.stop_reason,
            }
        if method == MCP_ELICITATION_CREATE_METHOD:
            handler = self._client_capability_state.elicitation_handler
            if handler is None:
                raise MCPProtocolError(
                    f"MCP server '{self.server_name}' requested elicitation without a declared elicitation handler."
                )
            result = handler.elicit(
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
            payload: dict[str, Any] = {"action": result.action}
            if result.content is not None:
                payload["content"] = dict(result.content)
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
            parts = normalized.split("/")
            primitive = parts[1] if len(parts) >= 2 else ""
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
        return

    def _normalize_call_result(
        self, *, remote_name: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        content_items = result.get("content", [])
        if not isinstance(content_items, list):
            content_items = []
        text_parts: list[str] = []
        normalized_content: list[dict[str, Any]] = []
        for item in content_items:
            if not isinstance(item, dict):
                continue
            normalized = dict(item)
            normalized_content.append(normalized)
            if str(normalized.get("type", "") or "").strip().lower() == "text":
                text = str(normalized.get("text", "") or "").strip()
                if text:
                    text_parts.append(text)
        structured_content = result.get("structuredContent")
        output_schema = dict(self._output_schemas_by_tool.get(remote_name, {}) or {})
        if output_schema and structured_content is not None:
            if not isinstance(structured_content, dict):
                raise MCPProtocolError(
                    f"MCP tool '{self.server_name}.{remote_name}' returned non-object structuredContent.",
                    reason_code="mcp_output_schema_invalid",
                )
            try:
                structured_content = validate_mcp_arguments(
                    schema=output_schema,
                    arguments=structured_content,
                )
            except Exception as exc:
                raise MCPProtocolError(
                    f"MCP tool '{self.server_name}.{remote_name}' returned invalid structuredContent.",
                    reason_code="mcp_output_schema_invalid",
                ) from exc
        content_text = "\n".join(text_parts).strip()
        if not content_text and structured_content is not None:
            try:
                content_text = json.dumps(
                    structured_content,
                    sort_keys=True,
                    default=str,
                )
            except Exception:
                content_text = str(structured_content)

        if bool(result.get("isError", False)):
            error_details: dict[str, Any] = {}
            stderr_tail = ""
            stderr_tail_fn = getattr(self._transport, "stderr_tail", None)
            if callable(stderr_tail_fn):
                stderr_tail = str(stderr_tail_fn() or "").strip()
            if stderr_tail:
                error_details["mcp_stderr_tail"] = stderr_tail
            structured_is_cancelled = False
            if isinstance(structured_content, dict):
                structured_is_cancelled = bool(structured_content.get("cancelled"))
            reason_code = (
                "mcp_client_cancelled"
                if structured_is_cancelled
                else "mcp_upstream_error"
            )
            raise MCPCallError(
                content_text
                or f"MCP tool '{self.server_name}.{remote_name}' returned an error.",
                reason_code=reason_code,
                details=error_details,
            )

        return {
            "ok": True,
            "verified": True,
            "content": content_text,
            "source": "mcp",
            "data": {
                "mcp_server": self.server_name,
                "mcp_remote_tool_name": remote_name,
                "content_items": normalized_content,
                "structured_content": structured_content,
                "output_schema": output_schema,
            },
        }

    def _normalize_prompt_result(
        self, *, remote_name: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        raw_messages = result.get("messages", [])
        if not isinstance(raw_messages, list):
            raw_messages = []
        normalized_messages: list[dict[str, Any]] = []
        text_parts: list[str] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            normalized = dict(item)
            normalized_messages.append(normalized)
            text_parts.extend(_collect_text_fragments(normalized.get("content")))
        description = str(result.get("description", "") or "").strip()
        content_text = "\n".join(part for part in text_parts if part).strip()
        if not content_text and description:
            content_text = description
        return {
            "ok": True,
            "verified": True,
            "content": content_text,
            "source": "mcp",
            "data": {
                "mcp_server": self.server_name,
                "mcp_remote_prompt_name": remote_name,
                "messages": normalized_messages,
                "description": description,
            },
        }

    def _normalize_resource_result(
        self, *, resource_uri: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        raw_contents = result.get("contents", [])
        if not isinstance(raw_contents, list):
            raw_contents = []
        normalized_contents: list[dict[str, Any]] = []
        text_parts: list[str] = []
        for item in raw_contents:
            if not isinstance(item, dict):
                continue
            normalized = dict(item)
            normalized_contents.append(normalized)
            text = str(normalized.get("text", "") or "").strip()
            if text:
                text_parts.append(text)
        return {
            "ok": True,
            "verified": True,
            "content": "\n".join(text_parts).strip(),
            "source": "mcp",
            "data": {
                "mcp_server": self.server_name,
                "mcp_resource_uri": resource_uri,
                "contents": normalized_contents,
            },
        }


def _build_prompt_arguments_schema(raw_arguments: Any) -> dict[str, Any]:
    if not isinstance(raw_arguments, list):
        return {"type": "object", "properties": {}, "additionalProperties": False}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for item in raw_arguments:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        if not name:
            continue
        properties[name] = {
            "type": "string",
            "description": str(item.get("description", "") or "").strip(),
        }
        if bool(item.get("required", False)):
            required.append(name)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = sorted(set(required))
    return schema


def _collect_text_fragments(content: Any) -> list[str]:
    if isinstance(content, str):
        text = content.strip()
        return [text] if text else []
    if isinstance(content, dict):
        text = str(content.get("text", "") or "").strip()
        if text:
            return [text]
        nested_parts: list[str] = []
        for value in content.values():
            nested_parts.extend(_collect_text_fragments(value))
        return nested_parts
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            parts.extend(_collect_text_fragments(item))
        return parts
    return []


def _coerce_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_completion_result(result: dict[str, Any]) -> MCPCompletionResult:
    completion = result.get("completion", {})
    if not isinstance(completion, dict):
        completion = {}
    raw_values = completion.get("values", [])
    values = tuple(str(item) for item in raw_values if isinstance(item, str))
    total = _coerce_optional_int(completion.get("total"))
    has_more = bool(completion.get("hasMore", False))
    return MCPCompletionResult(values=values, total=total, has_more=has_more)


def _build_transport(server: MCPServerConfig) -> Any:
    if server.transport == "streamable_http":
        return StreamableHTTPMCPTransport(server)
    return StdioMCPTransport(server)


def _resolve_mcp_tool_posture(
    *,
    server: MCPServerConfig,
    remote_name: str,
    annotations: dict[str, Any],
) -> MCPToolPosture:
    """Resolve policy-visible posture from MCP annotations and operator overrides."""
    min_scope = "WRITE_SAFE"
    dangerous = False
    idempotent = False

    if _annotation_bool(annotations, "readOnlyHint"):
        min_scope = "READ_ONLY"
        dangerous = False
        idempotent = True

    idempotent_hint = _optional_annotation_bool(annotations, "idempotentHint")
    if idempotent_hint is not None:
        idempotent = idempotent_hint

    if _annotation_bool(annotations, "openWorldHint") and min_scope == "READ_ONLY":
        min_scope = "WRITE_SAFE"

    if _annotation_bool(annotations, "destructiveHint"):
        min_scope = _stricter_mcp_scope(min_scope, "POWER_USER")
        dangerous = True
        idempotent = False

    for override in getattr(server, "tool_risk_overrides", []) or []:
        if not _mcp_risk_override_matches(
            pattern=str(getattr(override, "pattern", "") or ""),
            server_name=server.name,
            remote_name=remote_name,
        ):
            continue
        override_scope = str(getattr(override, "min_scope", "") or "").strip().upper()
        if override_scope:
            min_scope = override_scope
        override_dangerous = getattr(override, "dangerous", None)
        if override_dangerous is not None:
            dangerous = bool(override_dangerous)
        override_idempotent = getattr(override, "idempotent", None)
        if override_idempotent is not None:
            idempotent = bool(override_idempotent)

    return MCPToolPosture(
        min_scope=min_scope,
        dangerous=dangerous,
        idempotent=idempotent,
    )


def _annotation_bool(annotations: dict[str, Any], key: str) -> bool:
    return _optional_annotation_bool(annotations, key) is True


def _optional_annotation_bool(annotations: dict[str, Any], key: str) -> bool | None:
    value = annotations.get(key)
    if isinstance(value, bool):
        return value
    return None


def _stricter_mcp_scope(left: str, right: str) -> str:
    return left if _MCP_SCOPE_ORDER[left] >= _MCP_SCOPE_ORDER[right] else right


def _mcp_risk_override_matches(
    *,
    pattern: str,
    server_name: str,
    remote_name: str,
) -> bool:
    if not pattern:
        return False
    runtime_name = build_mcp_runtime_tool_name(
        server_name=server_name,
        remote_name=remote_name,
    )
    candidates = (
        remote_name,
        runtime_name.rsplit(".", 1)[-1],
        runtime_name,
    )
    return any(fnmatch.fnmatch(candidate, pattern) for candidate in candidates)
