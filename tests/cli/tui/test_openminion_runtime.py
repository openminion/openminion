from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.base.types import Message
from openminion.cli.parser.contracts import ensure_cli_component_compatibility
from openminion.cli.tui.project_context import ProjectContextInfo
from openminion.cli.tui.providers.runtime import OpenMinionRuntime
from openminion.cli.tui.widgets import MessageKind
from openminion.cli.tui.widgets.chat import format_chat_timestamp
from openminion.base.config.core import OpenMinionConfig


@dataclass
class _SessionRecord:
    id: str
    channel: str
    target: str
    status: str = "active"
    updated_at: str = "2026-03-21T00:00:00Z"


@dataclass
class _MessageRecord:
    id: str
    role: str
    body: str
    metadata: dict
    created_at: str


class _FakeSessions:
    def __init__(self) -> None:
        self._by_id: dict[str, _SessionRecord] = {}
        self._by_key: dict[tuple[str, str, str], str] = {}
        self._messages: dict[str, list[_MessageRecord]] = {}
        self._metadata: dict[str, dict[str, object]] = {}
        self._counter = 0

    def resolve_session(
        self,
        *,
        agent_id: str,
        channel: str,
        target: str,
        session_id: str | None = None,
        metadata: dict | None = None,
    ) -> _SessionRecord:
        if session_id:
            record = self._by_id.get(session_id)
            if record is None:
                record = _SessionRecord(id=session_id, channel=channel, target=target)
                self._by_id[session_id] = record
                self._messages.setdefault(session_id, [])
                self._metadata.setdefault(session_id, dict(metadata or {}))
            return record

        key = (agent_id, channel, target)
        existing_id = self._by_key.get(key)
        if existing_id:
            return self._by_id[existing_id]

        self._counter += 1
        sid = f"sess-{self._counter:03d}"
        record = _SessionRecord(id=sid, channel=channel, target=target)
        self._by_id[sid] = record
        self._by_key[key] = sid
        self._messages.setdefault(sid, [])
        self._metadata.setdefault(sid, dict(metadata or {}))
        return record

    def update_session_metadata(
        self, *, session_id: str, patch: dict[str, object]
    ) -> None:
        self._metadata.setdefault(session_id, {}).update(dict(patch))

    def get_session(self, session_id: str) -> _SessionRecord | None:
        return self._by_id.get(session_id)

    def list_sessions(
        self, *, limit: int = 100, newest_first: bool = True
    ) -> list[_SessionRecord]:
        items = list(self._by_id.values())
        return items[:limit]

    def list_messages(
        self, *, session_id: str, limit: int = 100, **_: object
    ) -> list[_MessageRecord]:
        return list(self._messages.get(session_id, []))[:limit]

    def add_message(
        self, session_id: str, *, role: str, body: str, metadata: dict | None = None
    ) -> None:
        entries = self._messages.setdefault(session_id, [])
        mid = f"m-{len(entries) + 1}"
        entries.append(
            _MessageRecord(
                id=mid,
                role=role,
                body=body,
                metadata=dict(metadata or {}),
                created_at="2026-03-21T10:21:00Z",
            )
        )


class _FakeGateway:
    def __init__(self, name: str) -> None:
        self._name = name
        self.calls: list[dict[str, object]] = []
        self.metadata: dict[str, str] = {}
        self.progress_events: list[dict[str, object]] = []

    async def handle_message(
        self,
        *,
        channel: str,
        target: str,
        body: str,
        session_id: str,
        inbound_metadata=None,
        progress_callback=None,
    ) -> Message:
        self.calls.append(
            {
                "channel": channel,
                "target": target,
                "body": body,
                "session_id": session_id,
                "inbound_metadata": dict(inbound_metadata or {}),
            }
        )
        if progress_callback is not None:
            for payload in self.progress_events:
                progress_callback(dict(payload))
        return Message(
            channel=channel,
            target=target,
            body=f"{self._name}:{body}",
            metadata=dict(self.metadata),
        )


class _FakeRuntime:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            default_agent="alpha",
            runtime=SimpleNamespace(session_context_token_budget=200000),
            agents={
                "alpha": SimpleNamespace(
                    name="alpha",
                    provider="openai",
                    default_channel="cli",
                ),
                "beta": SimpleNamespace(
                    name="beta",
                    provider="anthropic",
                    default_channel="cli",
                ),
            },
        )
        self.sessions = _FakeSessions()
        self.tools = SimpleNamespace(
            list=lambda: {
                "weather": SimpleNamespace(enabled=True),
                "exec.run": SimpleNamespace(enabled=False),
            }
        )
        self._gateways = {
            "alpha": _FakeGateway("alpha"),
            "beta": _FakeGateway("beta"),
        }

    def list_registered_agents(self) -> list[str]:
        return ["alpha", "beta"]

    def resolve_agent_profile(self, agent_id: str | None = None) -> SimpleNamespace:
        name = str(agent_id or "").strip() or "alpha"
        if name not in {"alpha", "beta"}:
            raise ValueError(name)
        return SimpleNamespace(name=name)

    def resolve_gateway(self, agent_id: str | None = None) -> _FakeGateway:
        name = str(agent_id or "").strip() or "alpha"
        return self._gateways[name]


class _FakeRuntimeNoConfigAgent(_FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.config = OpenMinionConfig(
            agents={
                "alpha": SimpleNamespace(
                    name="alpha",
                    provider="openai",
                    default_channel="console",
                ),
                "beta": SimpleNamespace(
                    name="beta",
                    provider="anthropic",
                    default_channel="console",
                ),
            },
            default_agent="alpha",
        )


@pytest.mark.asyncio
async def test_openminion_runtime_chat_contract_and_send_message() -> None:
    rt = _FakeRuntime()
    first = rt.sessions.resolve_session(agent_id="alpha", channel="cli", target="tui")
    rt.sessions.add_message(first.id, role="user", body="hello")
    rt.sessions.add_message(first.id, role="assistant", body="hi")

    tui_rt = OpenMinionRuntime(rt)

    ensure_cli_component_compatibility(tui_rt, component_type="chat_runtime")
    history = tui_rt.get_current_history()
    assert [m.kind for m in history] == [MessageKind.USER, MessageKind.AGENT]

    chunks = [chunk async for chunk in tui_rt.send_message("ping")]
    assert chunks == ["ping"]  # sender prefix stripped by _strip_sender_prefix
    assert rt.resolve_gateway("alpha").calls[-1]["session_id"] == tui_rt.session_id
    assert tui_rt.transport == "gateway"


@pytest.mark.asyncio
async def test_openminion_runtime_strips_timestamped_console_prefix_and_duplicate_line() -> (
    None
):
    rt = _FakeRuntime()
    gateway = rt.resolve_gateway("alpha")
    deliver_values: list[bool] = []

    async def _timestamped_handle_message(
        *,
        channel: str,
        target: str,
        body: str,
        session_id: str,
        deliver: bool = True,
        progress_callback=None,
    ) -> Message:
        del progress_callback
        deliver_values.append(deliver)
        gateway.calls.append(
            {
                "channel": channel,
                "target": target,
                "body": body,
                "session_id": session_id,
            }
        )
        return Message(
            channel=channel,
            target=target,
            body="[02:53:56Z] alpha: Hello there!\nHello there!",
            metadata={},
        )

    gateway.handle_message = _timestamped_handle_message
    tui_rt = OpenMinionRuntime(rt)

    chunks = [chunk async for chunk in tui_rt.send_message("ping")]
    assert chunks == ["Hello there!"]
    assert deliver_values == [False]


@pytest.mark.asyncio
async def test_openminion_runtime_marks_focus_returns_as_caller_delivered() -> None:
    rt = _FakeRuntime()
    gateway = rt.resolve_gateway("alpha")
    captured_metadata: list[dict[str, str]] = []

    async def _capture_handle_message(
        *,
        channel: str,
        target: str,
        body: str,
        session_id: str,
        inbound_metadata=None,
        deliver: bool = True,
        progress_callback=None,
    ) -> Message:
        del progress_callback
        gateway.calls.append(
            {
                "channel": channel,
                "target": target,
                "body": body,
                "session_id": session_id,
            }
        )
        captured_metadata.append(dict(inbound_metadata or {}))
        return Message(
            channel=channel,
            target=target,
            body=f"alpha:{body}",
            metadata={},
        )

    gateway.handle_message = _capture_handle_message
    focus_rt = OpenMinionRuntime(
        rt,
        target="focus",
        working_dir="/tmp/focus-ws",
    )

    chunks = [chunk async for chunk in focus_rt.send_message("ping")]
    assert chunks == ["ping"]
    assert captured_metadata == [
        {
            "workspace_root": str(Path("/tmp/focus-ws").resolve(strict=False)),
            "caller_handles_delivery": "true",
            "conversation_id": "focus-sess-001",
        }
    ]


@pytest.mark.asyncio
async def test_openminion_runtime_injects_project_context_once_per_session() -> None:
    rt = _FakeRuntime()
    gateway = rt.resolve_gateway("alpha")
    captured_metadata: list[dict[str, str]] = []

    async def _capture_handle_message(
        *,
        channel: str,
        target: str,
        body: str,
        session_id: str,
        inbound_metadata=None,
        deliver: bool = True,
        progress_callback=None,
    ) -> Message:
        del progress_callback, deliver
        gateway.calls.append(
            {
                "channel": channel,
                "target": target,
                "body": body,
                "session_id": session_id,
            }
        )
        captured_metadata.append(dict(inbound_metadata or {}))
        return Message(
            channel=channel,
            target=target,
            body=f"alpha:{body}",
            metadata={},
        )

    gateway.handle_message = _capture_handle_message
    focus_rt = OpenMinionRuntime(
        rt,
        target="focus",
        working_dir="/tmp/focus-ws",
    )
    focus_rt.set_project_context(
        ProjectContextInfo(
            path=Path("/tmp/focus-ws/OPENMINION.md"),
            source_name="OPENMINION.md",
            size_bytes=12,
            content="Follow repo rules.",
        )
    )

    _ = [chunk async for chunk in focus_rt.send_message("first")]
    _ = [chunk async for chunk in focus_rt.send_message("second")]

    assert captured_metadata[0]["project_context_name"] == "OPENMINION.md"
    assert captured_metadata[0]["project_context_body"] == "Follow repo rules."
    assert "project_context_name" not in captured_metadata[1]
    assert "project_context_body" not in captured_metadata[1]


@pytest.mark.asyncio
async def test_openminion_focus_runtime_reuses_stable_conversation_id() -> None:
    rt = _FakeRuntime()
    focus_rt = OpenMinionRuntime(
        rt,
        target="focus",
        working_dir="/tmp/focus-ws",
    )

    first_session_id = focus_rt.session_id
    _ = [chunk async for chunk in focus_rt.send_message("first")]
    _ = [chunk async for chunk in focus_rt.send_message("second")]

    calls = rt.resolve_gateway("alpha").calls
    first_metadata = calls[0]["inbound_metadata"]
    second_metadata = calls[1]["inbound_metadata"]

    assert isinstance(first_metadata, dict)
    assert isinstance(second_metadata, dict)
    assert first_metadata["conversation_id"] == f"focus-{first_session_id}"
    assert second_metadata["conversation_id"] == f"focus-{first_session_id}"
    assert first_metadata["caller_handles_delivery"] == "true"
    assert second_metadata["caller_handles_delivery"] == "true"


@pytest.mark.asyncio
async def test_openminion_runtime_rearms_project_context_on_new_session() -> None:
    rt = _FakeRuntime()
    gateway = rt.resolve_gateway("alpha")
    captured_metadata: list[dict[str, str]] = []

    async def _capture_handle_message(
        *,
        channel: str,
        target: str,
        body: str,
        session_id: str,
        inbound_metadata=None,
        deliver: bool = True,
        progress_callback=None,
    ) -> Message:
        del progress_callback, deliver
        gateway.calls.append(
            {
                "channel": channel,
                "target": target,
                "body": body,
                "session_id": session_id,
            }
        )
        captured_metadata.append(dict(inbound_metadata or {}))
        return Message(
            channel=channel,
            target=target,
            body=f"alpha:{body}",
            metadata={},
        )

    gateway.handle_message = _capture_handle_message
    focus_rt = OpenMinionRuntime(
        rt,
        target="focus",
        working_dir="/tmp/focus-ws",
    )
    focus_rt.set_project_context(
        ProjectContextInfo(
            path=Path("/tmp/focus-ws/OPENMINION.md"),
            source_name="OPENMINION.md",
            size_bytes=12,
            content="Follow repo rules.",
        )
    )

    first_session_id = focus_rt.session_id
    _ = [chunk async for chunk in focus_rt.send_message("first")]
    new_session_id = focus_rt.create_new_session()
    _ = [chunk async for chunk in focus_rt.send_message("second")]

    assert new_session_id != first_session_id
    assert captured_metadata[0]["project_context_name"] == "OPENMINION.md"
    assert captured_metadata[1]["project_context_name"] == "OPENMINION.md"
    assert captured_metadata[0]["conversation_id"] == f"focus-{first_session_id}"
    assert captured_metadata[1]["conversation_id"] == f"focus-{new_session_id}"
    assert gateway.calls[0]["session_id"] == first_session_id
    assert gateway.calls[1]["session_id"] == new_session_id


def test_openminion_runtime_reports_mcp_status_from_existing_subsystem() -> None:
    class _LiveSession:
        def list_tools(self):
            return [object()]

        def list_prompts(self):
            return [object()]

        def list_resources(self):
            return []

    rt = _FakeRuntime()
    rt.config.runtime.mcp_servers = [SimpleNamespace(name="fixture", transport="stdio")]
    rt.tools = SimpleNamespace(
        list=lambda: {
            "mcp.fixture.echo_text": SimpleNamespace(enabled=True),
            "mcp.fixture.prompt.greet_user": SimpleNamespace(enabled=True),
        },
        mcp_manager=SimpleNamespace(_sessions={"fixture": _LiveSession()}),
    )
    tui_rt = OpenMinionRuntime(rt)

    body = tui_rt.mcp_status_report()

    assert "MCP servers:" in body
    assert "fixture" in body
    assert "[ready]" in body
    assert "tools=1" in body
    assert "prompts=1" in body


def test_openminion_runtime_reports_mcp_errors_without_hiding_registered_tools() -> (
    None
):
    class _BrokenSession:
        def list_tools(self):
            raise RuntimeError("server unavailable")

        def list_prompts(self):
            raise AssertionError("unreachable")

        def list_resources(self):
            raise AssertionError("unreachable")

    rt = _FakeRuntime()
    rt.config.runtime.mcp_servers = [SimpleNamespace(name="fixture", transport="stdio")]
    rt.tools = SimpleNamespace(
        list=lambda: {
            "mcp.fixture.echo_text": SimpleNamespace(enabled=True),
        },
        mcp_manager=SimpleNamespace(_sessions={"fixture": _BrokenSession()}),
    )
    tui_rt = OpenMinionRuntime(rt)

    body = tui_rt.mcp_status_report()

    assert "[error]" in body
    assert "server unavailable" in body
    assert "mcp.fixture.echo_text" in body


@pytest.mark.asyncio
async def test_openminion_runtime_retries_retryable_contract_failure_once() -> None:
    rt = _FakeRuntime()
    gateway = rt.resolve_gateway("alpha")
    responses = iter(
        [
            Message(
                channel="cli",
                target="tui",
                body=(
                    "General act work ended without the required typed "
                    "finalization_status contract."
                ),
                metadata={},
            ),
            Message(
                channel="cli",
                target="tui",
                body="alpha:Recovered answer",
                metadata={},
            ),
        ]
    )

    async def _retry_handle_message(
        *,
        channel: str,
        target: str,
        body: str,
        session_id: str,
        progress_callback=None,
        deliver: bool = True,
    ) -> Message:
        del progress_callback, deliver
        gateway.calls.append(
            {
                "channel": channel,
                "target": target,
                "body": body,
                "session_id": session_id,
            }
        )
        return next(responses)

    gateway.handle_message = _retry_handle_message
    tui_rt = OpenMinionRuntime(rt)

    chunks = [chunk async for chunk in tui_rt.send_message("ping")]
    assert chunks == ["Recovered answer"]
    assert len(gateway.calls) == 2


@pytest.mark.asyncio
async def test_openminion_runtime_maps_retryable_contract_failure_after_retry() -> None:
    rt = _FakeRuntime()
    gateway = rt.resolve_gateway("alpha")

    async def _fail_handle_message(
        *,
        channel: str,
        target: str,
        body: str,
        session_id: str,
        inbound_metadata=None,
        progress_callback=None,
        deliver: bool = True,
    ) -> Message:
        del inbound_metadata, progress_callback, deliver
        gateway.calls.append(
            {
                "channel": channel,
                "target": target,
                "body": body,
                "session_id": session_id,
            }
        )
        return Message(
            channel=channel,
            target=target,
            body=(
                "General act work ended without the required typed "
                "finalization_status contract."
            ),
            metadata={},
        )

    gateway.handle_message = _fail_handle_message
    focus_rt = OpenMinionRuntime(
        rt,
        target="focus",
        working_dir="/tmp/focus-ws",
    )

    chunks = [chunk async for chunk in focus_rt.send_message("remember: x=y")]
    assert chunks == [
        "The model ended the turn without the required completion contract. Please try again."
    ]
    assert len(gateway.calls) == 2


@pytest.mark.asyncio
async def test_openminion_runtime_switch_agent_and_new_session() -> None:
    rt = _FakeRuntime()
    tui_rt = OpenMinionRuntime(rt)

    other = rt.sessions.resolve_session(
        agent_id="alpha",
        channel="cli",
        target="tui",
        session_id="sess-manual",
    )
    switched = tui_rt.switch_session(other.id)
    assert tui_rt.session_id == other.id
    assert switched == []

    tui_rt.switch_agent("beta")
    assert tui_rt.agent_id == "beta"
    chunks = [chunk async for chunk in tui_rt.send_message("hello")]
    assert chunks == ["hello"]  # sender prefix stripped by _strip_sender_prefix

    new_id = tui_rt.new_session()
    assert new_id.startswith("sess-")
    assert tui_rt.session_id == new_id
    assert rt.sessions.get_session(new_id) is not None

    tools = tui_rt.list_tools()
    assert tools == [("exec.run", False), ("weather", True)]


def test_format_chat_timestamp_prefers_relative_label_under_one_hour() -> None:
    now = datetime(2026, 3, 26, 12, 0, tzinfo=timezone.utc)
    older_local = (
        datetime.fromisoformat("2026-03-26T10:30:00+00:00")
        .astimezone()
        .strftime("%H:%M")
    )

    assert format_chat_timestamp("2026-03-26T11:58:00Z", now=now) == "2m ago"
    assert format_chat_timestamp("2026-03-26T10:30:00Z", now=now) == older_local


def test_openminion_runtime_preserves_created_at_for_header_formatting() -> None:
    rt = _FakeRuntime()
    first = rt.sessions.resolve_session(agent_id="alpha", channel="cli", target="tui")
    rt.sessions.add_message(first.id, role="assistant", body="hello")

    tui_rt = OpenMinionRuntime(rt)
    history = tui_rt.get_current_history()

    assert history[0].created_at == "2026-03-21T10:21:00Z"
    assert history[0].timestamp == ""


@pytest.mark.asyncio
async def test_openminion_runtime_supports_openminion_config_shape() -> None:
    rt = _FakeRuntimeNoConfigAgent()

    tui_rt = OpenMinionRuntime(rt, agent_id="beta")

    assert tui_rt.agent_id == "beta"
    chunks = [chunk async for chunk in tui_rt.send_message("hello")]
    assert chunks == ["hello"]
    assert rt.resolve_gateway("beta").calls[-1]["channel"] == "console"


@pytest.mark.asyncio
async def test_openminion_runtime_tracks_turn_and_session_token_usage() -> None:
    rt = _FakeRuntime()
    gateway = rt.resolve_gateway("alpha")
    gateway.metadata = {
        "total_input_tokens_used": "1200",
        "total_output_tokens_used": "300",
        "total_tokens_used": "1500",
    }
    tui_rt = OpenMinionRuntime(rt)

    before = tui_rt.token_usage_snapshot()
    assert before.turn_total_tokens is None
    assert before.session_total_tokens is None

    _ = [chunk async for chunk in tui_rt.send_message("ping")]
    first = tui_rt.token_usage_snapshot()
    assert first.turn_total_tokens == 1500
    assert first.session_total_tokens == 1500
    assert first.context_used_tokens == 1500
    assert first.context_limit_tokens == 200000
    assert first.turn_elapsed_seconds is not None

    _ = [chunk async for chunk in tui_rt.send_message("pong")]
    second = tui_rt.token_usage_snapshot()
    assert second.turn_total_tokens == 1500
    assert second.session_total_tokens == 3000
    assert second.context_used_tokens == 3000


@pytest.mark.asyncio
async def test_openminion_runtime_resets_session_usage_on_new_session_and_bind() -> (
    None
):
    rt = _FakeRuntime()
    gateway = rt.resolve_gateway("alpha")
    gateway.metadata = {
        "total_input_tokens_used": "40",
        "total_output_tokens_used": "2",
        "total_tokens_used": "42",
    }
    tui_rt = OpenMinionRuntime(rt)

    _ = [chunk async for chunk in tui_rt.send_message("hello")]
    assert tui_rt.token_usage_snapshot().session_total_tokens == 42

    new_id = tui_rt.create_new_session()
    reset_after_new = tui_rt.token_usage_snapshot()
    assert new_id.startswith("sess-")
    assert reset_after_new.turn_total_tokens is None
    assert reset_after_new.session_total_tokens is None
    assert reset_after_new.turn_elapsed_seconds is None

    other = rt.sessions.resolve_session(
        agent_id="alpha",
        channel="cli",
        target="tui",
        session_id="sess-existing",
    )
    tui_rt.bind_session(other.id)
    reset_after_bind = tui_rt.token_usage_snapshot()
    assert reset_after_bind.turn_total_tokens is None
    assert reset_after_bind.session_total_tokens is None


@pytest.mark.asyncio
async def test_openminion_runtime_throttles_live_usage_updates() -> None:
    rt = _FakeRuntime()
    gateway = rt.resolve_gateway("alpha")
    gateway.progress_events = [
        {"total_input_tokens_used": 100, "total_output_tokens_used": 20},
        {"total_input_tokens_used": 200, "total_output_tokens_used": 30},
        {"total_input_tokens_used": 500, "total_output_tokens_used": 50},
    ]
    gateway.metadata = {
        "total_input_tokens_used": "800",
        "total_output_tokens_used": "200",
        "total_tokens_used": "1000",
    }
    tui_rt = OpenMinionRuntime(rt)
    observed_turn_totals: list[int | None] = []
    ticks = itertools.chain([1.0, 1.1, 1.2, 1.3, 1.8, 2.0, 2.0], itertools.repeat(2.0))

    from openminion.cli.tui.providers import runtime as runtime_module

    original_monotonic = runtime_module.time.monotonic
    runtime_module.time.monotonic = lambda: next(ticks)
    try:
        _ = [
            chunk
            async for chunk in tui_rt.send_message(
                "stream",
                progress_callback=lambda _payload: observed_turn_totals.append(
                    tui_rt.token_usage_snapshot().turn_total_tokens
                ),
            )
        ]
    finally:
        runtime_module.time.monotonic = original_monotonic

    assert observed_turn_totals == [120, 120, 550]
    snapshot = tui_rt.token_usage_snapshot()
    assert snapshot.turn_total_tokens == 1000
    assert snapshot.session_total_tokens == 1000
    assert snapshot.has_live_deltas is False
    assert snapshot.turn_elapsed_seconds is not None
