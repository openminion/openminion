"""Local Agent Client Protocol v1 adapter over :class:`APIRuntime`."""

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from pathlib import Path
from typing import Any
from uuid import uuid4

from acp import Client, RequestError, run_agent
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AllowedOutcome,
    CloseSessionResponse,
    DeniedOutcome,
    ForkSessionResponse,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    McpCapabilities,
    NewSessionResponse,
    PermissionOption,
    PromptCapabilities,
    PromptResponse,
    RequestPermissionResponse,
    ResumeSessionResponse,
    ResourceContentBlock,
    SessionCapabilities,
    SessionInfo,
    SessionListCapabilities,
    TextContentBlock,
    ToolCallUpdate,
)

from openminion import __version__
from openminion.base.config.core import resolve_default_agent_id

_PROTOCOL_VERSION = 1
_SURFACE = "acp"


class OpenMinionACPAgent:
    """ACP's stable local lifecycle mapped to existing OpenMinion owners."""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime
        self._client: Client | None = None
        self._initialized = False
        self._active_traces: dict[str, str] = {}
        self._active_prompts: set[str] = set()

    def on_connect(self, client: Client) -> None:
        self._client = client

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: Any | None = None,
        client_info: Any | None = None,
        **_kwargs: Any,
    ) -> InitializeResponse:
        del client_capabilities, client_info
        self._initialized = True
        return InitializeResponse(
            protocol_version=_PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(
                load_session=True,
                prompt_capabilities=PromptCapabilities(
                    image=False,
                    audio=False,
                    embedded_context=False,
                ),
                mcp_capabilities=McpCapabilities(http=False, sse=False, acp=False),
                session_capabilities=SessionCapabilities(
                    list=SessionListCapabilities(),
                ),
            ),
            auth_methods=[],
            agent_info=Implementation(
                name="openminion",
                title="OpenMinion",
                version=__version__,
            ),
        )

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **_kwargs: Any,
    ) -> NewSessionResponse:
        self._require_initialized()
        self._reject_mcp(mcp_servers)
        self._reject_additional_directories(additional_directories)
        workspace = _workspace(cwd)
        session_id = uuid4().hex
        self._runtime.sessions.resolve_session(
            agent_id=resolve_default_agent_id(self._runtime.config),
            channel=_runtime_channel(self._runtime),
            target="local-client",
            session_id=session_id,
            metadata={"surface": _SURFACE, "workspace_root": str(workspace)},
        )
        return NewSessionResponse(session_id=session_id)

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        additional_directories: list[str] | None = None,
        **_kwargs: Any,
    ) -> LoadSessionResponse:
        self._require_initialized()
        self._reject_mcp(mcp_servers)
        self._reject_additional_directories(additional_directories)
        session = self._session(session_id)
        workspace = _workspace(cwd)
        if str(session.metadata.get("workspace_root", "")) != str(workspace):
            raise RequestError.invalid_params(
                {"reason": "workspace_mismatch", "sessionId": session_id}
            )
        return LoadSessionResponse()

    async def list_sessions(
        self,
        cwd: str | None = None,
        cursor: str | None = None,
        **_kwargs: Any,
    ) -> ListSessionsResponse:
        self._require_initialized()
        if cursor:
            raise RequestError.invalid_params({"reason": "cursor_not_supported"})
        workspace = str(_workspace(cwd)) if cwd else ""
        sessions = []
        for record in self._runtime.sessions.list_sessions(
            limit=100,
            metadata_filter={"surface": _SURFACE},
        ):
            record_workspace = str(record.metadata.get("workspace_root", ""))
            if workspace and record_workspace != workspace:
                continue
            sessions.append(
                SessionInfo(
                    session_id=record.id,
                    cwd=record_workspace,
                    title=str(record.metadata.get("title", "")) or None,
                    updated_at=record.updated_at,
                )
            )
        return ListSessionsResponse(sessions=sessions)

    async def close_session(
        self, session_id: str, **_kwargs: Any
    ) -> CloseSessionResponse:
        self._session(session_id)
        self._runtime.sessions.close_session(
            session_id=session_id,
            reason="acp_client_close",
        )
        return CloseSessionResponse()

    async def fork_session(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **_kwargs: Any,
    ) -> ForkSessionResponse:
        del session_id, cwd, additional_directories, mcp_servers
        raise RequestError.method_not_found("session/fork")

    async def resume_session(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **_kwargs: Any,
    ) -> ResumeSessionResponse:
        del session_id, cwd, additional_directories, mcp_servers
        raise RequestError.method_not_found("session/resume")

    async def prompt(
        self,
        session_id: str,
        prompt: list[Any],
        **_kwargs: Any,
    ) -> PromptResponse:
        session = self._session(session_id)
        if session.status == "closed":
            raise RequestError.invalid_params({"reason": "session_closed"})
        if session_id in self._active_prompts:
            raise RequestError.invalid_request(
                {"reason": "prompt_already_active", "sessionId": session_id}
            )
        message = _prompt_text(prompt)
        self._active_prompts.add(session_id)
        loop = asyncio.get_running_loop()
        updates: list[Future[Any]] = []
        streamed: list[str] = []

        def progress(payload: object) -> None:
            text, trace_id = _progress_text_and_trace(payload)
            if trace_id:
                self._active_traces[session_id] = trace_id
            if text and self._client is not None:
                streamed.append(text)
                updates.append(
                    asyncio.run_coroutine_threadsafe(
                        self._client.session_update(
                            session_id=session_id,
                            update=AgentMessageChunk(
                                session_update="agent_message_chunk",
                                content=TextContentBlock(type="text", text=text),
                            ),
                        ),
                        loop,
                    )
                )

        try:
            result = await asyncio.to_thread(
                self._runtime.run_turn,
                payload={
                    "message": message,
                    "session_id": session_id,
                    "channel": _runtime_channel(self._runtime),
                    "target": "local-client",
                    "inbound_metadata": {
                        "workspace_root": str(session.metadata["workspace_root"]),
                        "surface": _SURFACE,
                    },
                },
                progress_callback=progress,
                approval_callback=self._approval_callback(session_id, loop),
            )
            for update in updates:
                await asyncio.wrap_future(update)
            body = str(result.get("body", ""))
            if body and "".join(streamed) != body:
                await self._emit_text(session_id, body)
            return PromptResponse(stop_reason="end_turn")
        finally:
            self._active_traces.pop(session_id, None)
            self._active_prompts.discard(session_id)

    async def cancel(self, session_id: str, **_kwargs: Any) -> None:
        trace_id = self._active_traces.get(session_id, "")
        manager = getattr(self._runtime, "runtime_manager", None)
        if trace_id and manager is not None:
            manager.cancel_turn(trace_id)

    async def set_session_mode(
        self, session_id: str, mode_id: str, **_kwargs: Any
    ) -> None:
        del session_id, mode_id
        raise RequestError.method_not_found("session/set_mode")

    async def set_config_option(
        self, config_id: str, session_id: str, value: str | bool, **_kwargs: Any
    ) -> None:
        del config_id, session_id, value
        raise RequestError.method_not_found("session/set_config_option")

    async def authenticate(self, method_id: str, **_kwargs: Any) -> None:
        del method_id
        raise RequestError.method_not_found("authenticate")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        del params
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        del params
        raise RequestError.method_not_found(method)

    def _session(self, session_id: str) -> Any:
        self._require_initialized()
        session = self._runtime.sessions.get_session(str(session_id).strip())
        if session is None or session.metadata.get("surface") != _SURFACE:
            raise RequestError.resource_not_found(str(session_id))
        return session

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise RequestError.invalid_request({"reason": "initialize_required"})

    @staticmethod
    def _reject_mcp(mcp_servers: list[Any] | None) -> None:
        if mcp_servers:
            raise RequestError.invalid_params({"reason": "mcp_not_supported"})

    @staticmethod
    def _reject_additional_directories(directories: list[str] | None) -> None:
        if directories:
            raise RequestError.invalid_params(
                {"reason": "additional_directories_not_supported"}
            )

    def _approval_callback(
        self, session_id: str, client_loop: asyncio.AbstractEventLoop
    ) -> Callable[[str, dict[str, Any], Any], Awaitable[bool]]:
        async def approve(
            tool_name: str, args: dict[str, Any], approval_id: Any
        ) -> bool:
            request = asyncio.run_coroutine_threadsafe(
                self._request_permission(
                    session_id=session_id,
                    tool_name=tool_name,
                    args=args,
                    approval_id=approval_id,
                ),
                client_loop,
            )
            response = await asyncio.wrap_future(request)
            outcome = response.outcome
            return (
                isinstance(outcome, AllowedOutcome)
                and outcome.option_id == "allow_once"
            )

        return approve

    async def _request_permission(
        self,
        *,
        session_id: str,
        tool_name: str,
        args: dict[str, Any],
        approval_id: Any,
    ) -> RequestPermissionResponse:
        if self._client is None:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return await self._client.request_permission(
            session_id=session_id,
            tool_call=ToolCallUpdate(
                tool_call_id=str(approval_id or uuid4().hex),
                title=tool_name,
                raw_input=args,
                status="pending",
            ),
            options=[
                PermissionOption(
                    option_id="allow_once",
                    name="Allow once",
                    kind="allow_once",
                ),
                PermissionOption(
                    option_id="deny",
                    name="Deny",
                    kind="reject_once",
                ),
            ],
        )

    async def _emit_text(self, session_id: str, text: str) -> None:
        if self._client is None or not text:
            return
        await self._client.session_update(
            session_id=session_id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text=text),
            ),
        )


async def run_local_acp_agent(runtime: Any) -> None:
    """Serve one local ACP v1 connection over stdin/stdout."""

    # The 0.12 SDK routes ACP v1 session/close behind this switch even though
    # the method is in its v1 schema. Unapproved methods still fail closed in
    # OpenMinionACPAgent.
    await run_agent(OpenMinionACPAgent(runtime), use_unstable_protocol=True)


def _workspace(raw: str | None) -> Path:
    path = Path(str(raw or "")).expanduser()
    if not path.is_absolute():
        raise RequestError.invalid_params({"reason": "workspace_must_be_absolute"})
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RequestError.invalid_params({"reason": "workspace_not_found"}) from exc
    if not resolved.is_dir():
        raise RequestError.invalid_params({"reason": "workspace_not_directory"})
    return resolved


def _prompt_text(blocks: list[Any]) -> str:
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, TextContentBlock):
            parts.append(block.text)
        elif isinstance(block, ResourceContentBlock):
            parts.append(f"[resource: {block.uri}]")
        else:
            raise RequestError.invalid_params(
                {"reason": "unsupported_prompt_content", "type": block.type}
            )
    message = "\n".join(part for part in parts if part.strip()).strip()
    if not message:
        raise RequestError.invalid_params({"reason": "empty_prompt"})
    return message


def _runtime_channel(runtime: Any) -> str:
    agent_id = resolve_default_agent_id(runtime.config)
    profile = runtime.config.agents[agent_id]
    return str(profile.default_channel or "console")


def _progress_text_and_trace(payload: object) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return "", ""
    kind = str(payload.get("kind", ""))
    data = payload.get("data")
    values = data if isinstance(data, dict) else payload
    trace_id = str(payload.get("trace_id", "") or values.get("trace_id", ""))
    if kind not in {"token", "delta", "final_text", "assistant_token"}:
        return "", trace_id
    return str(values.get("text", "") or values.get("delta_text", "")), trace_id
