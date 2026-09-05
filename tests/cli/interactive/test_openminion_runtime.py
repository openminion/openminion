from __future__ import annotations

import asyncio
import io
import itertools
import threading
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from openminion.base.types import Message
from openminion.cli.parser.contracts import ensure_cli_component_compatibility
from openminion.cli.interactive.project_context import ProjectContextInfo
from openminion.cli.interactive.runtime import OpenMinionRuntime
from openminion.cli.interactive.runtime.messages import room_result_chat_messages
from openminion.cli.interactive.terminal.transcript import TerminalTranscript
from openminion.cli.presentation.models import MessageKind
from openminion.base.config.core import OpenMinionConfig


@dataclass
class _SessionRecord:
    id: str
    channel: str
    target: str
    status: str = "active"
    updated_at: str = "2026-03-21T00:00:00Z"
    session_key: str = ""
    metadata: dict[str, object] | None = None
    active_agent_id: str = ""


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
        self._events: dict[str, list[SimpleNamespace]] = {}
        self._participants: dict[tuple[str, str, str], SimpleNamespace] = {}

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
    ) -> _SessionRecord:
        self._metadata.setdefault(session_id, {}).update(dict(patch))
        record = self._by_id[session_id]
        record.metadata = {**dict(record.metadata or {}), **dict(patch)}
        return record

    def get_session(self, session_id: str) -> _SessionRecord | None:
        return self._by_id.get(session_id)

    def add_participant(
        self,
        *,
        session_id: str,
        participant_type: str,
        participant_id: str,
        channel: str,
        role: str,
        display_name: str,
    ) -> SimpleNamespace:
        participant = SimpleNamespace(
            participant_type=participant_type,
            participant_id=participant_id,
            channel=channel,
            role=role,
            display_name=display_name,
            left_at=None,
        )
        self._participants[(session_id, participant_type, participant_id)] = participant
        return participant

    def get_participant(
        self, session_id: str, participant_type: str, participant_id: str
    ) -> SimpleNamespace | None:
        participant = self._participants.get(
            (session_id, participant_type, participant_id)
        )
        if participant is not None and participant.left_at is None:
            return participant
        return None

    def list_participants(self, session_id: str) -> list[SimpleNamespace]:
        return [
            participant
            for (
                stored_session_id,
                _type,
                _id,
            ), participant in self._participants.items()
            if stored_session_id == session_id and participant.left_at is None
        ]

    def remove_participant(
        self, *, session_id: str, participant_type: str, participant_id: str
    ) -> bool:
        participant = self.get_participant(session_id, participant_type, participant_id)
        if participant is None:
            return False
        participant.left_at = "left"
        return True

    def set_active_agent(self, *, session_id: str, agent_id: str) -> _SessionRecord:
        if self.get_participant(session_id, "agent", agent_id) is None:
            raise ValueError(f"Agent {agent_id!r} is not an active participant")
        record = self._by_id[session_id]
        record.active_agent_id = agent_id
        return record

    def list_sessions(
        self,
        *,
        limit: int = 100,
        newest_first: bool = True,
        agent_id: str | None = None,
        target: str | None = None,
        metadata_filter: dict[str, object] | None = None,
    ) -> list[_SessionRecord]:
        del newest_first, agent_id
        items = list(self._by_id.values())
        if target:
            items = [item for item in items if item.target == target]
        if metadata_filter:
            items = [
                item
                for item in items
                if all(
                    (item.metadata or {}).get(key) == value
                    for key, value in metadata_filter.items()
                )
            ]
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

    def add_event(self, session_id: str, event_type: str, payload: dict) -> None:
        self._events.setdefault(session_id, []).insert(
            0,
            SimpleNamespace(event_type=event_type, payload=dict(payload)),
        )

    def list_events(self, *, session_id: str, **_: object) -> list[SimpleNamespace]:
        return list(self._events.get(session_id, []))


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

    def agent_discovery_snapshot(self) -> list[dict[str, object]]:
        return [
            {
                "agent_id": "alpha",
                "display_name": "Alpha Prime",
                "configured": True,
                "registry_present": False,
                "hot": True,
                "heartbeat_active": False,
                "available": True,
                "running": True,
                "stopped": False,
                "unknown": False,
                "state": "running",
            },
            {
                "agent_id": "beta",
                "display_name": "Beta",
                "configured": True,
                "registry_present": False,
                "hot": False,
                "heartbeat_active": False,
                "available": True,
                "running": False,
                "stopped": True,
                "unknown": False,
                "state": "configured",
            },
        ]

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


def _make_bound_room_runtime(
    *, role: str = "owner"
) -> tuple[_FakeRuntime, OpenMinionRuntime, SimpleNamespace]:
    rt = _FakeRuntime()
    room = rt.sessions.resolve_session(
        agent_id="alpha",
        channel="cli",
        target="focus",
        session_id=f"room-{role}",
    )
    room.session_key = f"room:{role}"
    room.metadata = {"local_human_id": "local-human"}
    actor = rt.sessions.add_participant(
        session_id=room.id,
        participant_type="human",
        participant_id="local-human",
        channel="cli",
        role=role,
        display_name="local-human",
    )
    focus_rt = OpenMinionRuntime(
        rt,
        target="focus",
        session_id=room.id,
    )
    return rt, focus_rt, actor


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
async def test_openminion_runtime_room_turn_uses_canonical_off_loop_payload() -> None:
    rt = _FakeRuntime()
    room = rt.sessions.resolve_session(
        agent_id="alpha",
        channel="cli",
        target="focus",
        session_id="custom-room-id",
    )
    room.session_key = "room:custom-room-id"
    room.metadata = {"local_human_id": "owner-local"}
    rt.sessions.add_participant(
        session_id=room.id,
        participant_type="human",
        participant_id="owner-local",
        channel="cli",
        role="owner",
        display_name="owner-local",
    )
    expected = {
        "agent_id": "beta",
        "body": "reviewed",
        "metadata": {"persisted_outbound_message_id": "out-2"},
    }
    calls: list[dict[str, object]] = []
    worker_threads: list[int] = []
    progress_threads: list[int] = []
    approval_threads: list[int] = []
    ui_thread = threading.get_ident()

    def run_turn(**kwargs):  # noqa: ANN003, ANN202
        calls.append(dict(kwargs))
        worker_threads.append(threading.get_ident())
        kwargs["progress_callback"]({"kind": "status", "label": "routing"})
        assert kwargs["approval_callback"]("file.write", {"path": "a"}, "call-1")
        return expected

    rt.run_turn = run_turn
    focus_rt = OpenMinionRuntime(
        rt,
        target="focus",
        working_dir="/tmp/focus-room",
        session_id=room.id,
    )

    async def approve(_tool: str, _args: dict, _call_id: object) -> bool:
        approval_threads.append(threading.get_ident())
        return True

    result = await focus_rt.run_room_turn(
        "review this",
        progress_callback=lambda _payload: progress_threads.append(
            threading.get_ident()
        ),
        approval_callback=approve,
        cancel_event=threading.Event(),
    )
    await asyncio.sleep(0)

    assert result is expected
    assert len(worker_threads) == 1
    assert worker_threads[0] != ui_thread
    assert progress_threads == [ui_thread]
    assert approval_threads == [ui_thread]
    payload = calls[0]["payload"]
    assert payload["session_id"] == room.id
    assert payload["deliver"] is False
    assert payload["inbound_metadata"]["participant_id"] == "owner-local"
    assert payload["inbound_metadata"]["caller_handles_delivery"] == "true"
    assert "conversation_id" not in payload["inbound_metadata"]


def test_room_result_uses_top_level_addressed_agent_and_persisted_id() -> None:
    messages = room_result_chat_messages(
        {
            "agent_id": "beta",
            "body": "Note: keep this exact text",
            "metadata": {"persisted_outbound_message_id": "out-beta"},
        }
    )

    assert [(item.sender, item.body, item.msg_id) for item in messages] == [
        ("beta", "Note: keep this exact text", "out-beta")
    ]


def test_room_history_reloads_persisted_agent_attribution() -> None:
    rt, focus_rt, _actor = _make_bound_room_runtime()
    rt.sessions.add_message(
        focus_rt.session_id,
        role="outbound",
        body="reviewed",
        metadata={"participant_id": "beta", "display_name": "Beta Reviewer"},
    )

    history = focus_rt.get_current_history()

    assert [(item.sender, item.body, item.show_header) for item in history] == [
        ("Beta Reviewer", "reviewed", True)
    ]
    output = io.StringIO()
    TerminalTranscript(
        Console(file=output, force_terminal=False, width=80)
    ).set_messages(history)
    assert "Beta Reviewer" in output.getvalue()


def test_room_session_lists_by_exact_agent_membership_and_exposes_facts() -> None:
    rt, focus_rt, _actor = _make_bound_room_runtime()
    session = rt.sessions.get_session(focus_rt.session_id)
    assert session is not None
    session.metadata = {
        "local_human_id": "local-human",
        "name": "Review room",
        "room_routing_mode": "sequential",
        "working_dir": "/tmp/room-work",
    }
    session.active_agent_id = "alpha"
    rt.sessions.add_participant(
        session_id=session.id,
        participant_type="agent",
        participant_id="alpha",
        channel="cli",
        role="participant",
        display_name="alpha",
    )
    focus_rt._working_dir = "/tmp/room-work"

    listed = focus_rt.list_sessions(scope="current_agent")
    directory = focus_rt.list_directory_sessions()

    assert [item.id for item in listed] == [session.id]
    assert listed[0].label == "Review room"
    assert listed[0].meta["room_routing_mode"] == "sequential"
    assert listed[0].meta["local_human_id"] == "local-human"
    assert listed[0].meta["participant_count"] == 2
    assert listed[0].meta["active_agent_id"] == "alpha"
    assert [item.id for item in directory] == [session.id]
    assert focus_rt.room_participants_report().startswith("Room: Review room\n")


def test_room_session_is_hidden_from_uninvited_agent_surface() -> None:
    rt, focus_rt, _actor = _make_bound_room_runtime()
    focus_rt._agent_id = "beta"

    assert focus_rt.list_sessions(scope="current_agent") == []


def test_room_owner_mutations_use_configured_agents_and_bounded_roles() -> None:
    rt, focus_rt, _actor = _make_bound_room_runtime()

    invited = focus_rt.room_invite_agent("beta")
    assert invited.role == "participant"
    assert invited.channel == "cli"
    focus_rt.room_activate("beta")
    assert rt.sessions.get_session(focus_rt.session_id).active_agent_id == "beta"

    before = list(rt.sessions.list_participants(focus_rt.session_id))
    with pytest.raises(ValueError, match="Unknown configured agent"):
        focus_rt.room_invite_agent("missing")
    with pytest.raises(ValueError, match="Invalid participant role"):
        focus_rt.room_invite_human("reviewer", role="admin")
    assert rt.sessions.list_participants(focus_rt.session_id) == before


@pytest.mark.asyncio
async def test_room_participant_can_post_but_cannot_mutate() -> None:
    rt, focus_rt, _actor = _make_bound_room_runtime(role="participant")
    calls: list[dict[str, object]] = []

    def run_turn(**kwargs):  # noqa: ANN003, ANN202
        calls.append(dict(kwargs))
        return {"agent_id": "alpha", "body": "ok", "metadata": {}}

    rt.run_turn = run_turn

    result = await focus_rt.run_room_turn(
        "hello",
        cancel_event=threading.Event(),
    )
    assert result["body"] == "ok"
    assert len(calls) == 1
    with pytest.raises(RuntimeError, match="require.*owner"):
        focus_rt.room_invite_agent("beta")


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["observer", "legacy-admin"])
async def test_room_non_posting_roles_stop_before_runtime_call(role: str) -> None:
    rt, focus_rt, _actor = _make_bound_room_runtime(role=role)
    calls: list[dict[str, object]] = []
    rt.run_turn = lambda **kwargs: calls.append(kwargs)

    with pytest.raises(RuntimeError):
        await focus_rt.run_room_turn("hello", cancel_event=threading.Event())

    assert calls == []


@pytest.mark.asyncio
async def test_departed_local_human_stops_before_runtime_call() -> None:
    rt, focus_rt, actor = _make_bound_room_runtime()
    actor.left_at = "left"
    calls: list[dict[str, object]] = []
    rt.run_turn = lambda **kwargs: calls.append(kwargs)

    with pytest.raises(RuntimeError, match="not an active room participant"):
        await focus_rt.run_room_turn("hello", cancel_event=threading.Event())

    assert calls == []


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
            "cwd": str(Path("/tmp/focus-ws").resolve(strict=False)),
            "caller_handles_delivery": "true",
            "conversation_id": "focus-sess-001",
            "resume": "true",
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
    assert first_metadata["resume"] == "true"
    assert second_metadata["resume"] == "true"


def test_openminion_focus_runtime_scopes_tool_profiles_to_brain_session() -> None:
    rt = _FakeRuntime()
    calls: list[tuple[str, str]] = []
    rt.tool_exposure_status = lambda *, session_id: (
        calls.append(("status", session_id)) or {"profiles": []}
    )
    rt.activate_tool_profile = lambda profile_id, **kwargs: (
        calls.append(("activate", kwargs["session_id"]))
        or {"profile_id": profile_id, "audit_id": "audit-1"}
    )
    rt.deactivate_tool_profile = lambda profile_id, **kwargs: (
        calls.append(("deactivate", kwargs["session_id"])) or True
    )
    focus_rt = OpenMinionRuntime(rt, target="focus")
    expected = f"{focus_rt.session_id}::conv:focus-{focus_rt.session_id}"

    focus_rt.tool_exposure_status()
    focus_rt.activate_tool_profile("security_readonly")
    focus_rt.deactivate_tool_profile("security_readonly")

    assert calls == [
        ("status", expected),
        ("activate", expected),
        ("deactivate", expected),
    ]


@pytest.mark.asyncio
async def test_openminion_focus_runtime_preserves_explicit_reset_metadata() -> None:
    rt = _FakeRuntime()
    focus_rt = OpenMinionRuntime(
        rt,
        target="focus",
        working_dir="/tmp/focus-ws",
    )

    _ = [
        chunk
        async for chunk in focus_rt.send_message(
            "start over",
            inbound_metadata={"reset_session": "true"},
        )
    ]

    metadata = rt.resolve_gateway("alpha").calls[0]["inbound_metadata"]
    assert isinstance(metadata, dict)
    assert metadata["reset_session"] == "true"
    assert "resume" not in metadata


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


def _mcp_status_manager(
    *,
    tools: tuple[str, ...] = ("echo-text",),
    prompts: tuple[str, ...] = (),
    failure: object | None = None,
    metrics: dict[str, int] | None = None,
) -> SimpleNamespace:
    snapshot = {
        "fixture": {
            "tool_names": tools,
            "prompt_names": prompts,
            "resource_uris": (),
            "resource_template_uris": (),
            "failure": failure,
            "recent_log": None,
            "metrics": metrics or {},
        }
    }
    return SimpleNamespace(server_status_snapshot=lambda: snapshot)


def test_openminion_runtime_reports_mcp_status_from_existing_subsystem() -> None:

    rt = _FakeRuntime()
    rt.config.runtime.mcp_servers = [SimpleNamespace(name="fixture", transport="stdio")]
    rt.tools = SimpleNamespace(
        list=lambda: {
            "mcp.fixture.echo_text": SimpleNamespace(enabled=True),
            "mcp.fixture.prompt.greet_user": SimpleNamespace(enabled=True),
        },
        mcp_manager=_mcp_status_manager(
            prompts=("greet-user",),
            metrics={
                "call_total": 2,
                "call_error_total": 1,
                "restart_total": 0,
            },
        ),
    )
    tui_rt = OpenMinionRuntime(rt)

    body = tui_rt.mcp_status_report()

    assert "MCP servers:" in body
    assert "fixture" in body
    assert "[ready]" in body
    assert "tools=1" in body
    assert "prompts=1" in body
    assert "activity: calls=2 errors=1 restarts=0" in body


def test_openminion_runtime_reports_mcp_errors_without_hiding_registered_tools() -> (
    None
):
    rt = _FakeRuntime()
    rt.config.runtime.mcp_servers = [SimpleNamespace(name="fixture", transport="stdio")]
    rt.tools = SimpleNamespace(
        list=lambda: {
            "mcp.fixture.echo_text": SimpleNamespace(enabled=True),
        },
        mcp_manager=_mcp_status_manager(
            failure=SimpleNamespace(message="server unavailable")
        ),
    )
    tui_rt = OpenMinionRuntime(rt)

    body = tui_rt.mcp_status_report()

    assert "[error]" in body
    assert "server unavailable" in body
    assert "echo-text" in body


def test_openminion_runtime_keeps_tool_only_mcp_server_ready() -> None:
    rt = _FakeRuntime()
    rt.config.runtime.mcp_servers = [SimpleNamespace(name="fixture", transport="stdio")]
    rt.tools = SimpleNamespace(
        list=lambda: {"mcp.fixture.echo_text": SimpleNamespace(enabled=True)},
        mcp_manager=_mcp_status_manager(),
    )
    tui_rt = OpenMinionRuntime(rt)

    body = tui_rt.mcp_status_report()

    assert "[ready]" in body
    assert "tools=1" in body
    assert "Method not found" not in body


@pytest.mark.parametrize(
    "failure_message",
    ["malformed prompts payload", "authorization failed"],
)
def test_openminion_runtime_reports_optional_mcp_failures(
    failure_message: str,
) -> None:
    rt = _FakeRuntime()
    rt.config.runtime.mcp_servers = [SimpleNamespace(name="fixture", transport="stdio")]
    rt.tools = SimpleNamespace(
        list=lambda: {"mcp.fixture.echo_text": SimpleNamespace(enabled=True)},
        mcp_manager=_mcp_status_manager(
            failure=SimpleNamespace(message=failure_message)
        ),
    )

    body = OpenMinionRuntime(rt).mcp_status_report()

    assert "[error]" in body
    assert failure_message in body


@pytest.mark.asyncio
async def test_openminion_runtime_preserves_model_failure_facts_without_retry() -> None:
    rt = _FakeRuntime()
    gateway = rt.resolve_gateway("alpha")
    failure_text = (
        "General act work ended without the required typed "
        "finalization_status contract."
    )

    async def _fail_handle_message(
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
        return Message(
            channel=channel,
            target=target,
            body=failure_text,
            metadata={},
        )

    gateway.handle_message = _fail_handle_message
    tui_rt = OpenMinionRuntime(rt)

    chunks = [chunk async for chunk in tui_rt.send_message("ping")]
    assert chunks == [failure_text]
    assert len(gateway.calls) == 1


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


def test_openminion_runtime_agent_sidebar_uses_discovery_truth() -> None:
    rt = _FakeRuntime()
    tui_rt = OpenMinionRuntime(rt)

    agents = tui_rt.list_agents()

    assert [agent.id for agent in agents] == ["alpha", "beta"]
    assert agents[0].label == "Alpha Prime"
    assert agents[0].active is True
    assert agents[0].meta["configured"] is True
    assert agents[0].meta["running"] is True
    assert agents[1].meta["running"] is False


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


def test_openminion_runtime_reports_latest_context_budget_and_compaction() -> None:
    rt = _FakeRuntime()
    tui_rt = OpenMinionRuntime(rt)
    rt.sessions.add_event(
        tui_rt.session_id,
        "session.context.budget",
        {
            "max_tokens": 8000,
            "budget_source": "runtime_cap",
            "selected_recent_count": 10,
        },
    )
    rt.sessions.add_event(
        tui_rt.session_id,
        "session.context.compaction",
        {"compacted_count": 3, "reason": "token_pressure"},
    )

    snapshot = tui_rt.context_budget_snapshot()

    assert snapshot["max_tokens"] == 8000
    assert snapshot["selected_recent_count"] == 10
    assert snapshot["compacted_count"] == 3
    assert snapshot["compaction_reason"] == "token_pressure"


def test_openminion_runtime_renders_durable_token_usage() -> None:
    rt = _FakeRuntime()
    tui_rt = OpenMinionRuntime(rt)
    rt.sessions.add_event(
        tui_rt.session_id,
        "llm.call.completed",
        {
            "provider": "openai",
            "model": "gpt-test",
            "usage": {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10},
            "cost_usd": 0.001,
            "cost_source": "provider",
        },
    )

    report = tui_rt.token_usage_report()

    assert "provider=10" in report
    assert "provider_cost=$0.001" in report


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

    from openminion.cli.interactive import runtime as runtime_module

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
