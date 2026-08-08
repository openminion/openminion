from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Event

import pytest
from acp import RequestError, connect_to_agent, run_agent
from acp._transport import memory_transport_pair
from acp.schema import (
    AllowedOutcome,
    DeniedOutcome,
    RequestPermissionResponse,
    TextContentBlock,
)

from openminion.api.operations.acp import OpenMinionACPAgent
from openminion.base.config import OpenMinionConfig
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database


class _Client:
    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow
        self.updates: list[tuple[str, object]] = []
        self.permission_requests: list[tuple[str, object, list[object]]] = []

    async def session_update(self, session_id: str, update: object, **_kwargs) -> None:
        self.updates.append((session_id, update))

    async def request_permission(
        self,
        session_id: str,
        tool_call: object,
        options: list[object],
        **_kwargs,
    ) -> RequestPermissionResponse:
        self.permission_requests.append((session_id, tool_call, options))
        if self.allow:
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id="allow_once")
            )
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))


class _Manager:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    def cancel_turn(self, trace_id: str) -> bool:
        self.cancelled.append(trace_id)
        return True


class _Runtime:
    def __init__(self, sessions: SessionStore) -> None:
        self.sessions = sessions
        self.config = OpenMinionConfig.from_dict(
            {
                "agents": {"default": {"name": "default", "provider": "echo"}},
                "default_agent": "default",
            }
        )
        self.runtime_manager = _Manager()
        self.approval_result = False

    def run_turn(
        self,
        *,
        payload: dict[str, object],
        progress_callback,
        approval_callback,
    ) -> dict[str, object]:
        progress_callback(
            {"kind": "token", "trace_id": "trace-1", "data": {"text": "hello"}}
        )
        self.approval_result = asyncio.run(
            approval_callback("file.write", {"path": "result.txt"}, "approval-1")
        )
        return {"body": "hello", "session_id": payload["session_id"]}


class _BlockingRuntime(_Runtime):
    def __init__(self, sessions: SessionStore) -> None:
        super().__init__(sessions)
        self.started = Event()
        self.release = Event()

    def run_turn(
        self,
        *,
        payload: dict[str, object],
        progress_callback,
        approval_callback,
    ) -> dict[str, object]:
        del approval_callback
        progress_callback({"kind": "token", "trace_id": "trace-1", "data": {}})
        self.started.set()
        self.release.wait(timeout=5)
        return {"body": "complete", "session_id": payload["session_id"]}


@pytest.fixture
def runtime(tmp_path: Path):
    database_path = tmp_path / "state" / "openminion.db"
    migrate_database(database_path)
    connection = connect_database(database_path)
    value = _Runtime(SessionStore(connection))
    try:
        yield value
    finally:
        connection.close()


async def _initialized_agent(runtime: _Runtime, client: _Client):
    agent = OpenMinionACPAgent(runtime)
    agent.on_connect(client)
    response = await agent.initialize(protocol_version=1)
    return agent, response


@pytest.mark.asyncio
async def test_acp_lifecycle_uses_durable_session_owner(
    runtime: _Runtime, tmp_path: Path
) -> None:
    agent, initialized = await _initialized_agent(runtime, _Client())

    created = await agent.new_session(cwd=str(tmp_path))
    listed = await agent.list_sessions(cwd=str(tmp_path))
    loaded = await agent.load_session(cwd=str(tmp_path), session_id=created.session_id)
    closed = await agent.close_session(created.session_id)

    assert initialized.protocol_version == 1
    assert initialized.agent_capabilities.load_session is True
    assert [item.session_id for item in listed.sessions] == [created.session_id]
    assert loaded is not None
    assert closed is not None
    record = runtime.sessions.get_session(created.session_id)
    assert record is not None
    assert record.status == "closed"
    assert record.metadata == {"surface": "acp", "workspace_root": str(tmp_path)}


@pytest.mark.asyncio
async def test_acp_prompt_streams_and_routes_approval(
    runtime: _Runtime, tmp_path: Path
) -> None:
    client = _Client(allow=True)
    agent, _ = await _initialized_agent(runtime, client)
    created = await agent.new_session(cwd=str(tmp_path))

    result = await agent.prompt(
        created.session_id,
        [TextContentBlock(type="text", text="say hello")],
    )

    assert result.stop_reason == "end_turn"
    assert runtime.approval_result is True
    assert len(client.permission_requests) == 1
    assert [update.content.text for _, update in client.updates] == ["hello"]


@pytest.mark.asyncio
async def test_acp_permission_denial_is_fail_closed(
    runtime: _Runtime, tmp_path: Path
) -> None:
    client = _Client(allow=False)
    agent, _ = await _initialized_agent(runtime, client)
    created = await agent.new_session(cwd=str(tmp_path))

    await agent.prompt(
        created.session_id,
        [TextContentBlock(type="text", text="request tool")],
    )

    assert runtime.approval_result is False


@pytest.mark.asyncio
async def test_acp_permission_round_trip_uses_sdk_transport(
    runtime: _Runtime, tmp_path: Path
) -> None:
    client = _Client(allow=True)
    client_transport, agent_transport = memory_transport_pair()
    server_task = asyncio.create_task(
        run_agent(
            OpenMinionACPAgent(runtime),
            agent_transport,
            use_unstable_protocol=True,
        )
    )
    connection = connect_to_agent(
        client,
        client_transport,
        use_unstable_protocol=True,
    )
    try:
        await connection.initialize(protocol_version=1)
        created = await connection.new_session(cwd=str(tmp_path))
        await asyncio.wait_for(
            connection.prompt(
                created.session_id,
                [TextContentBlock(type="text", text="request approval")],
            ),
            timeout=5,
        )
    finally:
        await connection.close()
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)

    assert runtime.approval_result is True
    assert len(client.permission_requests) == 1


@pytest.mark.asyncio
async def test_acp_rejects_invalid_workspace_mcp_and_workspace_mismatch(
    runtime: _Runtime, tmp_path: Path
) -> None:
    agent, _ = await _initialized_agent(runtime, _Client())

    with pytest.raises(RequestError, match="Invalid params"):
        await agent.new_session(cwd="relative")
    with pytest.raises(RequestError, match="Invalid params"):
        await agent.new_session(cwd=str(tmp_path), mcp_servers=[object()])
    with pytest.raises(RequestError, match="Invalid params"):
        await agent.new_session(
            cwd=str(tmp_path), additional_directories=[str(tmp_path)]
        )

    created = await agent.new_session(cwd=str(tmp_path))
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(RequestError, match="Invalid params"):
        await agent.load_session(cwd=str(other), session_id=created.session_id)


@pytest.mark.asyncio
async def test_acp_requires_initialize_and_rejects_unknown_session(
    runtime: _Runtime, tmp_path: Path
) -> None:
    agent = OpenMinionACPAgent(runtime)
    with pytest.raises(RequestError, match="Invalid request"):
        await agent.new_session(cwd=str(tmp_path))

    await agent.initialize(protocol_version=1)
    with pytest.raises(RequestError, match="Resource not found"):
        await agent.load_session(cwd=str(tmp_path), session_id="missing")


@pytest.mark.asyncio
async def test_acp_rejects_unadvertised_fork_and_resume(
    runtime: _Runtime, tmp_path: Path
) -> None:
    agent, _ = await _initialized_agent(runtime, _Client())
    created = await agent.new_session(cwd=str(tmp_path))

    with pytest.raises(RequestError, match="Method not found"):
        await agent.fork_session(created.session_id, str(tmp_path))
    with pytest.raises(RequestError, match="Method not found"):
        await agent.resume_session(created.session_id, str(tmp_path))


@pytest.mark.asyncio
async def test_acp_rejects_concurrent_prompt_and_cancels_active_trace(
    runtime: _Runtime, tmp_path: Path
) -> None:
    blocking = _BlockingRuntime(runtime.sessions)
    agent, _ = await _initialized_agent(blocking, _Client())
    created = await agent.new_session(cwd=str(tmp_path))
    prompt = [TextContentBlock(type="text", text="long turn")]
    task = asyncio.create_task(agent.prompt(created.session_id, prompt))
    assert await asyncio.to_thread(blocking.started.wait, 5)

    with pytest.raises(RequestError, match="Invalid request"):
        await agent.prompt(created.session_id, prompt)
    await agent.cancel(created.session_id)
    blocking.release.set()
    await task

    assert blocking.runtime_manager.cancelled == ["trace-1"]


def test_acp_cli_command_is_registered() -> None:
    from openminion.cli.parser.base import build_parser

    args = build_parser(selected_command="acp").parse_args(["acp"])

    assert args.needs_app is True
    assert callable(args.handler)
