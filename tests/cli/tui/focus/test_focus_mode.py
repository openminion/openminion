from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from textual.widgets import Input, OptionList

from openminion.base.types import Message
from openminion.services.gateway.streaming import GatewayStreamEvent
from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION
from openminion.cli.tui.focus import FocusApp
from openminion.cli.tui.focus.screen import (
    FocusDebugPane,
    FocusScreen,
)
from openminion.cli.tui.focus.widgets import (
    FocusStatusLine,
    SessionOverlay,
    ToolApprovalWidget,
    ToolBlockWidget,
    ToolsOverlay,
)
from openminion.cli.tui.providers.runtime import OpenMinionRuntime
from openminion.cli.tui.widgets import ChatMessage, ChatSearchBar
from openminion.cli.tui.focus.widgets import (
    FocusComposer,
    FocusMessageWidget,
    FocusTranscript,
)
from openminion.cli.tui.presentation.models import MessageKind
from openminion.services.bootstrap.onboarding import (
    OnboardingAction,
    OnboardingState,
    OnboardingStatus,
    OnboardingTrack,
)


@dataclass
class _SessionRecord:
    id: str
    channel: str
    target: str
    agent_id: str = "alpha"
    updated_at: str = "2026-04-02T09:00:00+00:00"
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class _MessageRecord:
    id: str
    role: str
    body: str
    metadata: dict[str, Any]
    created_at: str = "2026-04-02T09:00:00+00:00"


class _FakeSessions:
    def __init__(self) -> None:
        self._counter = 0
        self._records: dict[str, _SessionRecord] = {}
        self._messages: dict[str, list[_MessageRecord]] = {}
        self._lane_to_id: dict[tuple[str, str, str], str] = {}

    def resolve_session(
        self,
        *,
        agent_id: str,
        channel: str,
        target: str,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> _SessionRecord:
        normalized_agent = str(agent_id or "").strip() or "alpha"
        normalized_channel = str(channel or "").strip() or "console"
        normalized_target = str(target or "").strip() or "focus"
        if session_id:
            record = self._records.get(session_id)
            if record is None:
                record = _SessionRecord(
                    id=session_id,
                    channel=normalized_channel,
                    target=normalized_target,
                    agent_id=normalized_agent,
                    metadata=dict(metadata or {}),
                )
                self._records[session_id] = record
                self._messages.setdefault(session_id, [])
            return record

        key = (normalized_agent, normalized_channel, normalized_target)
        existing = self._lane_to_id.get(key)
        if existing:
            return self._records[existing]

        self._counter += 1
        created = _SessionRecord(
            id=f"focus-{self._counter:03d}",
            channel=normalized_channel,
            target=normalized_target,
            agent_id=normalized_agent,
            metadata=dict(metadata or {}),
        )
        self._records[created.id] = created
        self._messages.setdefault(created.id, [])
        self._lane_to_id[key] = created.id
        return created

    def get_session(self, session_id: str) -> _SessionRecord | None:
        return self._records.get(session_id)

    def update_session_metadata(
        self, *, session_id: str, patch: dict[str, Any]
    ) -> None:
        record = self._records[session_id]
        record.metadata.update(dict(patch or {}))

    def list_sessions(
        self,
        *,
        limit: int = 100,
        newest_first: bool = True,
        agent_id: str | None = None,
        target: str | None = None,
        metadata_filter: dict[str, str] | None = None,
        **_: Any,
    ) -> list[_SessionRecord]:
        items = list(self._records.values())
        if target:
            items = [item for item in items if item.target == target]
        if metadata_filter:
            for key, value in metadata_filter.items():
                items = [
                    item
                    for item in items
                    if str(item.metadata.get(key, "") or "") == str(value or "")
                ]
        if agent_id:
            normalized_agent = str(agent_id or "").strip()
            items = [item for item in items if item.agent_id == normalized_agent]
        items.sort(key=lambda item: (item.updated_at, item.id), reverse=newest_first)
        return items[:limit]

    def list_messages(
        self, *, session_id: str, limit: int = 100, **_: Any
    ) -> list[_MessageRecord]:
        return list(self._messages.get(session_id, []))[:limit]

    def add_message(
        self,
        session_id: str,
        *,
        role: str,
        body: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        messages = self._messages.setdefault(session_id, [])
        messages.append(
            _MessageRecord(
                id=f"{session_id}-m{len(messages) + 1}",
                role=role,
                body=body,
                metadata=dict(metadata or {}),
            )
        )

    def count_sessions(self) -> int:
        return len(self._records)


class _FakeGateway:
    def __init__(self, name: str) -> None:
        self._name = name
        self.calls: list[dict[str, Any]] = []

    async def handle_message(
        self,
        *,
        channel: str,
        target: str,
        body: str,
        session_id: str,
        progress_callback=None,
        inbound_metadata=None,
        approval_callback=None,
    ) -> Message:
        self.calls.append(
            {
                "channel": channel,
                "target": target,
                "body": body,
                "session_id": session_id,
                "progress_callback": progress_callback,
                "approval_callback": approval_callback,
                "inbound_metadata": dict(inbound_metadata or {}),
            }
        )
        return Message(channel=channel, target=target, body=f"{self._name}:{body}")


class _FakeStreamingGateway(_FakeGateway):
    async def handle_message_streaming(
        self,
        *,
        channel: str,
        target: str,
        body: str,
        session_id: str,
        inbound_metadata=None,
        deliver: bool = True,
        approval_callback=None,
    ):
        self.calls.append(
            {
                "channel": channel,
                "target": target,
                "body": body,
                "session_id": session_id,
                "approval_callback": approval_callback,
                "inbound_metadata": dict(inbound_metadata or {}),
                "deliver": deliver,
            }
        )
        yield GatewayStreamEvent(
            trace_id="stream-1",
            kind="tool_call_started",
            tool_name="exec.run",
            args={"command": "pwd"},
            call_id="stream-call-1",
        )
        yield GatewayStreamEvent(
            trace_id="stream-1",
            kind="tool_call_completed",
            tool_name="exec.run",
            args={"command": "pwd"},
            call_id="stream-call-1",
            ok=True,
            duration_ms=12,
            exit_code=0,
            text=session_id,
        )
        yield GatewayStreamEvent(
            trace_id="stream-1", kind="assistant_token", text="hi "
        )
        yield GatewayStreamEvent(
            trace_id="stream-1",
            kind="assistant_token",
            text="there",
        )
        yield GatewayStreamEvent(
            trace_id="stream-1",
            kind="final_message",
            final_message={
                "channel": channel,
                "target": target,
                "body": f"{self._name}:{body}",
                "metadata": {"total_tokens": 3},
            },
        )


class _FakeRuntime:
    def __init__(self, *, streaming_gateway: bool = False) -> None:
        self._agent_profiles = {
            "alpha": SimpleNamespace(name="alpha", provider="openai"),
            "beta": SimpleNamespace(name="beta", provider="anthropic"),
            "custom-agent": SimpleNamespace(name="custom-agent", provider="cerebras"),
        }
        self.config = SimpleNamespace(
            agent=SimpleNamespace(
                name="alpha",
                default_channel="console",
                provider="openai",
            ),
            providers=SimpleNamespace(
                openai=SimpleNamespace(model="gpt-4.1-mini"),
                anthropic=SimpleNamespace(model="claude-3-5-sonnet-latest"),
                cerebras=SimpleNamespace(model="gpt-oss-120b"),
            ),
        )
        self.sessions = _FakeSessions()
        self.tools = SimpleNamespace(
            list=lambda: {
                "exec.run": SimpleNamespace(enabled=True),
                "file.read": SimpleNamespace(enabled=True),
            }
        )
        self._gateways: dict[str, _FakeGateway] = {}
        self._streaming_gateway = streaming_gateway

    def list_registered_agents(self) -> list[str]:
        return ["alpha", "beta", "custom-agent"]

    def resolve_agent_profile(self, agent_id: str | None = None) -> SimpleNamespace:
        normalized = str(agent_id or "").strip() or "alpha"
        return self._agent_profiles.get(
            normalized,
            SimpleNamespace(name=normalized, provider="openai"),
        )

    def resolve_gateway(self, agent_id: str | None = None) -> _FakeGateway:
        name = str(agent_id or "").strip() or "alpha"
        if name not in self._gateways:
            gateway_class = (
                _FakeStreamingGateway if self._streaming_gateway else _FakeGateway
            )
            self._gateways[name] = gateway_class(name)
        return self._gateways[name]


class _FocusRuntimeDouble:
    contract_version = CLI_INTERFACE_VERSION

    def __init__(
        self,
        *,
        working_dir: str,
        agent_id: str = "alpha",
        provider_name: str = "openai",
        model_name: str = "gpt-4.1-mini",
        session_id: str | None = None,
        history_by_session: dict[str, list[ChatMessage]] | None = None,
        candidate: _SessionRecord | None = None,
        directory_sessions: list[_SessionRecord] | None = None,
        approval_tool: bool = False,
    ) -> None:
        self._working_dir = str(Path(working_dir).resolve(strict=False))
        self._agent_id = agent_id
        self._provider_name = str(provider_name or "").strip()
        self._model_name = str(model_name or "").strip()
        self._session_id = str(session_id or "")
        self._history_by_session = {
            key: list(value) for key, value in (history_by_session or {}).items()
        }
        self._candidate = candidate
        self._directory_sessions = list(
            directory_sessions or ([candidate] if candidate is not None else [])
        )
        self._approval_tool = approval_tool
        self._session_counter = len(self._history_by_session)
        self.last_send_kwargs: dict[str, Any] | None = None
        self.last_approval_result: bool | None = None
        self.tool_list = [("exec.run", True), ("file.read", True), ("fetch.get", False)]

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def transport(self) -> str:
        return "gateway"

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def is_bound(self) -> bool:
        return bool(self._session_id)

    @property
    def working_dir(self) -> str:
        return self._working_dir

    def get_current_history(self) -> list[ChatMessage]:
        return list(self._history_by_session.get(self._session_id, []))

    def list_sessions(self) -> list[Any]:
        return []

    def list_agents(self) -> list[Any]:
        return []

    def list_tools(self) -> list[tuple[str, bool]]:
        return list(self.tool_list)

    def switch_session(self, session_id: str) -> list[ChatMessage]:
        self.bind_session(session_id)
        return self.get_current_history()

    def switch_agent(self, agent_id: str) -> None:
        self._agent_id = str(agent_id or "").strip() or self._agent_id

    def new_session(self) -> str:
        return self.create_new_session()

    def bind_session(self, session_id: str) -> None:
        self._session_id = str(session_id or "").strip()
        self._history_by_session.setdefault(self._session_id, [])

    def create_new_session(self) -> str:
        self._session_counter += 1
        self._session_id = f"focus-test-{self._session_counter:03d}"
        self._history_by_session.setdefault(self._session_id, [])
        return self._session_id

    def find_candidate_session(self) -> _SessionRecord | None:
        return self._candidate

    def list_directory_sessions(self, *, limit: int = 20) -> list[_SessionRecord]:
        return list(self._directory_sessions[:limit])

    async def send_message(
        self,
        text: str,
        *,
        progress_callback=None,
        inbound_metadata=None,
        approval_callback=None,
    ):
        self.last_send_kwargs = {
            "text": text,
            "progress_callback": progress_callback,
            "approval_callback": approval_callback,
            "inbound_metadata": dict(inbound_metadata or {}),
        }
        if self._approval_tool and approval_callback is not None:
            approved = bool(
                await approval_callback("exec.run", {"command": "pwd"}, "call-1")
            )
            self.last_approval_result = approved
            if approved and progress_callback is not None:
                progress_callback(
                    {
                        "kind": "tool_started",
                        "tool_name": "exec.run",
                        "args": {"command": "pwd"},
                        "call_id": "call-1",
                    }
                )
                progress_callback(
                    {
                        "kind": "tool_completed",
                        "tool_name": "exec.run",
                        "args": {"command": "pwd"},
                        "call_id": "call-1",
                        "content": self._working_dir,
                        "duration_ms": 12,
                        "exit_code": 0,
                    }
                )
                yield "Ran pwd."
                return
            yield "Denied."
            return
        if progress_callback is not None:
            progress_callback({"phase": "respond", "label": "Working..."})
        yield f"Echo: {text}"


async def _collect_chunks(
    runtime: OpenMinionRuntime, text: str, **kwargs: Any
) -> list[str]:
    return [chunk async for chunk in runtime.send_message(text, **kwargs)]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _ready_onboarding_status() -> OnboardingStatus:
    root = _repo_root()
    return OnboardingStatus(
        state=OnboardingState.READY,
        action=OnboardingAction.CONTINUE,
        track=OnboardingTrack.CLOUD,
        reason="ready",
        config_path=root / "test-configs" / "per-agent.json",
        home_root=root,
        data_root=root / ".openminion",
    )


def test_focus_parser_registers_options_and_handler() -> None:
    from openminion.cli.commands.focus import run_focus
    from openminion.cli.parser.base import build_parser

    parser = build_parser()
    args = parser.parse_args(
        ["focus", "--agent", "alpha", "--session", "focus-1", "--dir", "/tmp/work"]
    )

    assert args.command == "focus"
    assert args.agent == "alpha"
    assert args.session == "focus-1"
    assert args.dir == "/tmp/work"
    assert args.no_interactive is False
    assert args.handler is run_focus
    assert args.needs_app is False


@pytest.mark.asyncio
async def test_openminion_runtime_focus_deferred_binding_and_send_message_forwarding() -> (
    None
):
    rt = _FakeRuntime()
    runtime = OpenMinionRuntime(
        rt,
        target="focus",
        working_dir="/tmp/focus-project",
        bind_immediately=False,
    )

    assert runtime.is_bound is False
    assert rt.sessions.count_sessions() == 0
    assert runtime.provider_name == "openai"
    assert runtime.model_name == "gpt-4.1-mini"
    with pytest.raises(RuntimeError):
        await _collect_chunks(runtime, "hello")

    created = runtime.create_new_session()
    assert runtime.is_bound is True
    assert created == runtime.session_id
    record = rt.sessions.get_session(created)
    assert record is not None
    assert record.metadata["working_dir"] == str(Path("/tmp/focus-project").resolve())
    assert record.metadata["focus_mode"] is True

    async def _approve(tool_name: str, args: dict[str, Any], call_id: Any) -> bool:
        del tool_name, args, call_id
        return True

    progress_events: list[dict[str, Any]] = []
    chunks = await _collect_chunks(
        runtime,
        "ping",
        progress_callback=progress_events.append,
        inbound_metadata={"request_source": "test"},
        approval_callback=_approve,
    )

    assert chunks == ["ping"]
    call = rt.resolve_gateway("alpha").calls[-1]
    assert call["target"] == "focus"
    assert callable(call["progress_callback"])
    assert getattr(call["progress_callback"], "__self__", None) is progress_events
    assert call["approval_callback"] is _approve
    assert call["inbound_metadata"]["request_source"] == "test"
    assert call["inbound_metadata"]["workspace_root"] == str(
        Path("/tmp/focus-project").resolve()
    )


@pytest.mark.asyncio
async def test_openminion_runtime_focus_uses_gateway_streaming_when_available() -> None:
    rt = _FakeRuntime(streaming_gateway=True)
    runtime = OpenMinionRuntime(
        rt,
        target="focus",
        working_dir="/tmp/focus-streaming-project",
        bind_immediately=False,
    )
    runtime.create_new_session()

    async def _approve(tool_name: str, args: dict[str, Any], call_id: Any) -> bool:
        del tool_name, args, call_id
        return True

    progress_events: list[dict[str, Any]] = []
    chunks = await _collect_chunks(
        runtime,
        "stream please",
        progress_callback=progress_events.append,
        inbound_metadata={"request_source": "stream-test"},
        approval_callback=_approve,
    )

    assert chunks == ["hi ", "there"]
    call = rt.resolve_gateway("alpha").calls[-1]
    assert call["deliver"] is False
    assert call["approval_callback"] is _approve
    assert call["inbound_metadata"]["request_source"] == "stream-test"
    assert progress_events[0]["kind"] == "tool_started"
    assert progress_events[0]["tool_name"] == "exec.run"
    assert progress_events[0]["call_id"] == "stream-call-1"
    assert progress_events[1]["kind"] == "tool_completed"
    assert progress_events[1]["call_id"] == "stream-call-1"


@pytest.mark.asyncio
async def test_focus_screen_streaming_tool_progress_updates_single_inline_block() -> (
    None
):
    runtime = OpenMinionRuntime(
        _FakeRuntime(streaming_gateway=True),
        target="focus",
        working_dir="/tmp/focus-streaming-ui",
        bind_immediately=False,
    )
    runtime.create_new_session()
    app = FocusApp(runtime=runtime, working_dir=runtime.working_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        input_widget = app.screen.query_one("#focus-input", Input)
        input_widget.value = "stream please"
        input_widget.focus()
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause()
            if app.screen._busy is False:
                break

        blocks = list(app.screen.query(ToolBlockWidget))
        assert len(blocks) == 1
        tool_block = blocks[0]
        assert not getattr(tool_block, "_pending", True)
        all_msgs = list(app.screen.query_one(FocusTranscript)._messages)
        assert not any(message.kind == MessageKind.TOOL for message in all_msgs)
        parent_message = tool_block.parent
        assert isinstance(parent_message, FocusMessageWidget)
        assert parent_message._message.kind == MessageKind.AGENT


def test_openminion_runtime_focus_explicit_session_and_candidate_lookup() -> None:
    rt = _FakeRuntime()
    working_dir = str(Path("/tmp/focus-project").resolve())
    explicit = rt.sessions.resolve_session(
        agent_id="custom-agent",
        channel="console",
        target="focus",
        session_id="focus-explicit",
        metadata={"working_dir": working_dir},
    )
    rt.sessions.resolve_session(
        agent_id="custom-agent",
        channel="console",
        target="tui",
        session_id="tui-other",
        metadata={"working_dir": working_dir},
    )
    candidate = rt.sessions.resolve_session(
        agent_id="custom-agent",
        channel="console",
        target="focus",
        session_id="focus-candidate",
        metadata={"working_dir": working_dir},
    )
    candidate.updated_at = "2026-04-02T12:00:00+00:00"

    runtime = OpenMinionRuntime(
        rt,
        target="focus",
        agent_id="custom-agent",
        working_dir=working_dir,
        bind_immediately=False,
        session_id=explicit.id,
    )

    assert runtime.is_bound is True
    assert runtime.agent_id == "custom-agent"
    assert runtime.session_id == explicit.id
    assert runtime.provider_name == "cerebras"
    assert runtime.model_name == "gpt-oss-120b"

    runtime = OpenMinionRuntime(
        rt,
        target="focus",
        agent_id="custom-agent",
        working_dir=working_dir,
        bind_immediately=False,
    )
    found = runtime.find_candidate_session()
    assert found is not None
    assert found.id == candidate.id
    assert runtime.is_bound is False


def test_openminion_runtime_focus_creates_fresh_explicit_session_when_deferred() -> (
    None
):
    rt = _FakeRuntime()
    working_dir = str(Path("/tmp/focus-project").resolve())

    runtime = OpenMinionRuntime(
        rt,
        target="focus",
        agent_id="custom-agent",
        working_dir=working_dir,
        bind_immediately=False,
        session_id="focus-fresh-explicit",
    )

    assert runtime.is_bound is True
    assert runtime.session_id == "focus-fresh-explicit"
    record = rt.sessions.get_session("focus-fresh-explicit")
    assert record is not None
    assert record.agent_id == "custom-agent"
    assert record.target == "focus"
    assert record.metadata["working_dir"] == working_dir
    assert record.metadata["focus_mode"] is True


def test_openminion_runtime_focus_history_expands_tool_events_and_relative_paths() -> (
    None
):
    rt = _FakeRuntime()
    working_dir = str(Path("/tmp/focus-project").resolve())
    session = rt.sessions.resolve_session(
        agent_id="alpha",
        channel="console",
        target="focus",
        session_id="focus-history",
        metadata={"working_dir": working_dir},
    )
    rt.sessions.add_message(
        session.id,
        role="assistant",
        body="alpha:Done reading the file.",
        metadata={
            "tool_results": [
                {
                    "tool_name": "file.read",
                    "args": {
                        "path": str(Path(working_dir) / "tests" / "test_focus_mode.py")
                    },
                    "content": "line 1\nline 2",
                }
            ]
        },
    )

    runtime = OpenMinionRuntime(
        rt,
        target="focus",
        working_dir=working_dir,
        bind_immediately=False,
        session_id=session.id,
    )
    history = runtime.get_current_history()

    assert len(history) == 2
    assert history[0].kind == MessageKind.TOOL
    assert history[0].tool_event is not None
    assert history[0].tool_event.tool_name == "file.read"
    assert history[0].tool_event.args["path"] == "tests/test_focus_mode.py"
    assert history[1].kind == MessageKind.AGENT
    assert history[1].body == "Done reading the file."


@pytest.mark.asyncio
async def test_focus_app_mounts_single_screen_without_dashboard_chrome() -> None:
    runtime = _FocusRuntimeDouble(
        working_dir="/tmp/focus-app",
        session_id="focus-bound",
        history_by_session={"focus-bound": []},
    )
    app = FocusApp(runtime=runtime, working_dir=runtime.working_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        assert isinstance(app.screen, FocusScreen)
        # top FocusHeader was deleted; chrome consolidated
        # into FocusStatusLine. Mount check shifts accordingly.
        assert app.screen.query_one(FocusStatusLine)
        assert app.screen.query_one(FocusTranscript)
        assert app.screen.query_one(FocusComposer)
        assert app.screen.query_one(FocusStatusLine)
        assert app.screen.query_one(FocusDebugPane)
        # runtime label moved from `#focus-header-runtime` into
        # the consolidated `FocusStatusLine` `model:` segment.
        status_text = app.screen.query_one(FocusStatusLine)._text()
        assert "openai" in status_text
        assert "gpt-4.1-mini" in status_text
        assert list(app.screen.query("TabbedContent")) == []
        assert list(app.screen.query("#sidebar")) == []


@pytest.mark.asyncio
async def test_focus_app_input_preserves_space_key() -> None:
    runtime = _FocusRuntimeDouble(
        working_dir="/tmp/focus-space",
        session_id="focus-bound",
        history_by_session={"focus-bound": []},
    )
    app = FocusApp(runtime=runtime, working_dir=runtime.working_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        input_widget = app.screen.query_one("#focus-input", Input)
        input_widget.focus()
        await pilot.press("w", "h", "a", "t", "space", "n", "o", "w")
        await pilot.pause()

        assert input_widget.value == "what now"


@pytest.mark.asyncio
async def test_focus_app_input_preserves_space_key_after_turn() -> None:
    runtime = _FocusRuntimeDouble(
        working_dir="/tmp/focus-space-after-turn",
        session_id="focus-bound",
        history_by_session={"focus-bound": []},
    )
    app = FocusApp(runtime=runtime, working_dir=runtime.working_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        input_widget = app.screen.query_one("#focus-input", Input)
        input_widget.value = "hi"
        input_widget.focus()
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause()
            if app.screen._busy is False:
                break

        input_widget.focus()
        await pilot.press("w", "h", "a", "t", "space", "n", "o", "w")
        await pilot.pause()

        assert input_widget.value == "what now"


@pytest.mark.asyncio
async def test_focus_screen_resume_prompt_binds_candidate_and_loads_history() -> None:
    candidate = _SessionRecord(id="focus-old", channel="console", target="focus")
    history = {
        "focus-old": [
            ChatMessage(
                kind=MessageKind.AGENT,
                sender="alpha",
                body="Welcome back.",
            )
        ]
    }
    runtime = _FocusRuntimeDouble(
        working_dir="/tmp/focus-resume",
        candidate=candidate,
        history_by_session=history,
    )
    app = FocusApp(runtime=runtime, working_dir=runtime.working_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        assert app.screen.query_one(".focus-inline-prompt")
        await pilot.press("y")
        await pilot.pause()
        await pilot.pause()

        assert runtime.session_id == "focus-old"
        messages = app.screen.query_one(FocusTranscript)._messages
        assert any(message.body == "Welcome back." for message in messages)


@pytest.mark.asyncio
async def test_focus_screen_disables_input_until_session_is_bound() -> None:
    candidate = _SessionRecord(id="focus-old", channel="console", target="focus")
    runtime = _FocusRuntimeDouble(
        working_dir="/tmp/focus-bind",
        candidate=candidate,
        history_by_session={"focus-old": []},
    )
    app = FocusApp(runtime=runtime, working_dir=runtime.working_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        input_widget = app.screen.query_one("#focus-input", Input)
        assert input_widget.disabled is True
        assert runtime.is_bound is False

        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("hey"))
        await pilot.pause()
        assert not any(
            message.kind == MessageKind.USER and message.body == "hey"
            for message in app.screen.query_one(FocusTranscript)._messages
        )

        await pilot.press("y")
        await pilot.pause()
        await pilot.pause()

        assert runtime.session_id == "focus-old"
        assert input_widget.disabled is False


@pytest.mark.asyncio
async def test_focus_screen_tool_approval_flow_renders_tool_block() -> None:
    runtime = _FocusRuntimeDouble(
        working_dir="/tmp/focus-approval",
        session_id="focus-approval",
        history_by_session={"focus-approval": []},
        approval_tool=True,
    )
    app = FocusApp(runtime=runtime, working_dir=runtime.working_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        input_widget = app.screen.query_one("#focus-input", Input)
        input_widget.value = "run pwd"
        input_widget.focus()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert app.screen.query_one(ToolApprovalWidget)
        await pilot.press("y")
        # Approval triggers `_run_turn` continuation which yields the
        # final "Ran pwd." chunk asynchronously. Drain the event loop
        # until the AGENT message lands or we hit the bound.
        for _ in range(20):
            await pilot.pause()
            if any(
                message.kind == MessageKind.AGENT and message.body == "Ran pwd."
                for message in app.screen.query_one(FocusTranscript)._messages
            ):
                break

        assert runtime.last_approval_result is True
        tool_block = app.screen.query_one(ToolBlockWidget)
        assert not list(app.screen.query(ToolApprovalWidget))
        all_msgs = list(app.screen.query_one(FocusTranscript)._messages)
        agent_bodies = [(m.kind, m.body) for m in all_msgs]
        assert any(
            message.kind == MessageKind.AGENT and message.body == "Ran pwd."
            for message in all_msgs
        ), f"AGENT 'Ran pwd.' missing; messages={agent_bodies}"
        assert not any(message.kind == MessageKind.TOOL for message in all_msgs)
        parent_message = tool_block.parent
        assert isinstance(parent_message, FocusMessageWidget)
        assert parent_message._message.kind == MessageKind.AGENT


@pytest.mark.asyncio
async def test_focus_screen_shortcuts_cover_search_debug_tools_sessions_and_new_session() -> (
    None
):
    directory_sessions = [
        _SessionRecord(
            id="focus-switch",
            channel="console",
            target="focus",
            updated_at="2026-04-02T10:00:00+00:00",
        )
    ]
    runtime = _FocusRuntimeDouble(
        working_dir="/tmp/focus-shortcuts",
        session_id="focus-active",
        history_by_session={
            "focus-active": [
                ChatMessage(kind=MessageKind.AGENT, sender="alpha", body="hello")
            ],
            "focus-switch": [
                ChatMessage(kind=MessageKind.AGENT, sender="alpha", body="switched")
            ],
        },
        directory_sessions=directory_sessions,
    )
    app = FocusApp(runtime=runtime, working_dir=runtime.working_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        app.screen.query_one(FocusTranscript).focus()
        await pilot.press("ctrl+f")
        await pilot.pause()
        assert app.screen.query_one(ChatSearchBar).display is True

        app.screen.action_toggle_debug()
        await pilot.pause()
        assert not app.screen.query_one(FocusDebugPane).has_class("--hidden")

        await pilot.press("ctrl+t")
        await pilot.pause()
        tools_overlay = app.screen
        assert isinstance(tools_overlay, ToolsOverlay)
        option_list = tools_overlay.query_one("#focus-tools-overlay-list", OptionList)
        assert "exec.run" in str(option_list.get_option_at_index(0).prompt)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, FocusScreen)

        await pilot.press("ctrl+s")
        await pilot.pause()
        sessions_overlay = app.screen
        assert isinstance(sessions_overlay, SessionOverlay)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert runtime.session_id == "focus-switch"
        assert any(
            message.body == "switched"
            for message in app.screen.query_one(FocusTranscript)._messages
        )

        await pilot.press("ctrl+n")
        await pilot.pause()
        assert runtime.session_id.startswith("focus-test-")
        assert runtime.session_id != "focus-switch"
        assert app.screen._runtime.session_id == runtime.session_id


@pytest.mark.asyncio
async def test_focus_escape_prompts_before_exit_and_can_cancel() -> None:
    runtime = _FocusRuntimeDouble(
        working_dir="/tmp/focus-exit",
        session_id="focus-exit",
        history_by_session={"focus-exit": []},
    )
    app = FocusApp(runtime=runtime, working_dir=runtime.working_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        app.screen.query_one(FocusTranscript).focus()
        app.screen.action_handle_escape()
        await pilot.pause()
        assert app.screen.query_one(".focus-inline-prompt")
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, FocusScreen)


def test_run_focus_live_wires_shared_runtime_and_closes(monkeypatch) -> None:
    # this test exercises the Textual --rich path which now
    # has a non-TTY guard. Pytest stdin/stdout aren't TTYs, so mock
    # them True to clear the guard before reaching the FocusApp stub.
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    focus_command = importlib.import_module("openminion.cli.commands.focus")
    from openminion.api.runtime import APIRuntime

    captured: dict[str, Any] = {}
    closed: list[bool] = []

    class _FakeApiRuntime(SimpleNamespace):
        def close(self) -> None:
            closed.append(True)

    class _FakeFocusRuntime:
        def __init__(self, runtime, **kwargs) -> None:
            captured["runtime"] = runtime
            captured["runtime_kwargs"] = dict(kwargs)

        def set_project_context(self, info) -> None:
            captured["project_context"] = info

    class _FakeApp:
        def __init__(self, **kwargs) -> None:
            captured["app_kwargs"] = dict(kwargs)

        def run(self) -> None:
            return None

    monkeypatch.setattr(
        "openminion.cli.commands.tui._inspect_tui_onboarding",
        lambda args: _ready_onboarding_status(),
    )
    monkeypatch.setattr(
        APIRuntime,
        "from_config_path",
        staticmethod(lambda *args, **kwargs: _FakeApiRuntime()),
    )
    monkeypatch.setattr(
        "openminion.cli.tui.providers.OpenMinionRuntime", _FakeFocusRuntime
    )
    monkeypatch.setattr("openminion.cli.tui.focus.FocusApp", _FakeApp)

    args = SimpleNamespace(
        config=str(
            (_repo_root() / "test-configs" / "per-agent.json").resolve(strict=False)
        ),
        home_root=str(_repo_root()),
        data_root=None,
        agent="alpha",
        session="focus-explicit",
        dir="/tmp/focus-live",
        rich=True,
    )

    assert focus_command.run_focus(args) == 0
    assert captured["runtime_kwargs"]["target"] == "focus"
    assert captured["runtime_kwargs"]["agent_id"] == "alpha"
    assert captured["runtime_kwargs"]["session_id"] == "focus-explicit"
    assert captured["runtime_kwargs"]["bind_immediately"] is False
    assert captured["app_kwargs"]["working_dir"] == str(
        Path("/tmp/focus-live").resolve()
    )
    assert closed == [True]


def test_run_terminal_wires_shared_runtime_and_closes(monkeypatch) -> None:
    focus_command = importlib.import_module("openminion.cli.commands.focus")
    from openminion.api.runtime import APIRuntime

    captured: dict[str, Any] = {}
    closed: list[bool] = []

    class _FakeApiRuntime(SimpleNamespace):
        def close(self) -> None:
            closed.append(True)

    class _FakeFocusRuntime:
        def __init__(self, runtime, **kwargs) -> None:
            captured["runtime"] = runtime
            captured["runtime_kwargs"] = dict(kwargs)
            self.agent_id = kwargs.get("agent_id") or "alpha"
            self.session_id = kwargs.get("session_id") or "focus-auto"
            self.provider_name = "openai"
            self.model_name = "MiniMax-M2.7"
            self.transport = "gateway"

        def set_project_context(self, info) -> None:
            captured["project_context"] = info

    def _fake_run_terminal_focus(runtime, **kwargs) -> int:
        captured["terminal_runtime"] = runtime
        captured["terminal_kwargs"] = dict(kwargs)
        return 0

    monkeypatch.setattr(
        "openminion.cli.commands.tui._inspect_tui_onboarding",
        lambda args: _ready_onboarding_status(),
    )
    monkeypatch.setattr(
        APIRuntime,
        "from_config_path",
        staticmethod(lambda *args, **kwargs: _FakeApiRuntime()),
    )
    monkeypatch.setattr(
        "openminion.cli.tui.providers.OpenMinionRuntime", _FakeFocusRuntime
    )
    monkeypatch.setattr(
        "openminion.cli.tui.terminal.run_terminal_focus",
        _fake_run_terminal_focus,
    )

    args = SimpleNamespace(
        config=str(
            (_repo_root() / "test-configs" / "per-agent.json").resolve(strict=False)
        ),
        home_root=str(_repo_root()),
        data_root=None,
        agent="alpha",
        session="focus-explicit",
        dir="/tmp/focus-live",
        rich=False,
    )

    assert focus_command.run_focus(args) == 0
    assert captured["runtime_kwargs"]["target"] == "focus"
    assert captured["runtime_kwargs"]["agent_id"] == "alpha"
    assert captured["runtime_kwargs"]["session_id"] == "focus-explicit"
    assert captured["runtime_kwargs"]["bind_immediately"] is True
    assert captured["terminal_runtime"] is not captured["runtime"]
    assert captured["terminal_kwargs"]["working_dir"] == str(
        Path("/tmp/focus-live").resolve()
    )
    assert closed == [True]


def test_run_terminal_defers_update_notice_until_shell_owner(monkeypatch) -> None:
    focus_command = importlib.import_module("openminion.cli.commands.focus")
    from openminion.api.runtime import APIRuntime

    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []

    class _FakeApiRuntime(SimpleNamespace):
        def close(self) -> None:
            return None

    class _FakeFocusRuntime:
        def __init__(self, runtime, **kwargs) -> None:
            self.api_runtime = runtime
            self.agent_id = kwargs.get("agent_id") or "alpha"
            self.session_id = kwargs.get("session_id") or "focus-auto"
            self.provider_name = "openai"
            self.model_name = "MiniMax-M2.7"
            self.transport = "gateway"

        def set_project_context(self, info) -> None:
            captured["project_context"] = info

    def _resolver() -> str:
        resolver_calls.append("called")
        return "Update available"

    def _fake_run_terminal_focus(runtime, **kwargs) -> int:
        del runtime
        captured["startup_notice"] = kwargs.get("startup_notice")
        assert resolver_calls == []
        return 0

    monkeypatch.setattr(
        "openminion.cli.commands.tui._inspect_tui_onboarding",
        lambda args: _ready_onboarding_status(),
    )
    monkeypatch.setattr(
        APIRuntime,
        "from_config_path",
        staticmethod(lambda *args, **kwargs: _FakeApiRuntime()),
    )
    monkeypatch.setattr(
        "openminion.cli.tui.providers.OpenMinionRuntime", _FakeFocusRuntime
    )
    monkeypatch.setattr(
        "openminion.cli.tui.terminal.run_terminal_focus",
        _fake_run_terminal_focus,
    )
    monkeypatch.setattr(
        focus_command,
        "_build_update_notice_resolver",
        lambda args: _resolver,
    )

    args = SimpleNamespace(
        config=str(
            (_repo_root() / "test-configs" / "per-agent.json").resolve(strict=False)
        ),
        home_root=str(_repo_root()),
        data_root=None,
        agent="alpha",
        session="focus-explicit",
        dir="/tmp/focus-live",
        rich=False,
    )

    assert focus_command.run_focus(args) == 0
    assert callable(captured["startup_notice"])
    assert resolver_calls == []
    assert captured["startup_notice"]() == "Update available"
    assert resolver_calls == ["called"]


def test_run_terminal_uses_runtime_data_root_for_history_path(
    monkeypatch, tmp_path
) -> None:
    from openminion.api.runtime import APIRuntime

    focus_command = importlib.import_module("openminion.cli.commands.focus")
    history_root = tmp_path / "openminion-data-root"
    captured: dict[str, Any] = {}

    class _FakeApiRuntime(SimpleNamespace):
        data_root = history_root

        def close(self) -> None:
            return None

    class _FakeFocusRuntime:
        def __init__(self, runtime, **kwargs) -> None:
            self.api_runtime = runtime
            self.agent_id = kwargs.get("agent_id") or "alpha"
            self.session_id = kwargs.get("session_id") or "focus-auto"
            self.provider_name = "openai"
            self.model_name = "MiniMax-M2.7"
            self.transport = "gateway"

        def set_project_context(self, info) -> None:
            captured["project_context"] = info

    def _fake_run_terminal_focus(runtime, **kwargs) -> int:
        from openminion.cli.tui.terminal.shell import _focus_history_path

        captured["history_path"] = _focus_history_path(runtime)
        return 0

    monkeypatch.setattr(
        "openminion.cli.commands.tui._inspect_tui_onboarding",
        lambda args: _ready_onboarding_status(),
    )
    monkeypatch.setattr(
        APIRuntime,
        "from_config_path",
        staticmethod(lambda *args, **kwargs: _FakeApiRuntime()),
    )
    monkeypatch.setattr(
        "openminion.cli.tui.providers.OpenMinionRuntime", _FakeFocusRuntime
    )
    monkeypatch.setattr(
        "openminion.cli.tui.terminal.run_terminal_focus",
        _fake_run_terminal_focus,
    )

    args = SimpleNamespace(
        config=str(
            (_repo_root() / "test-configs" / "per-agent.json").resolve(strict=False)
        ),
        home_root=str(_repo_root()),
        data_root=str(history_root),
        agent="alpha",
        session="focus-explicit",
        dir="/tmp/focus-live",
        rich=False,
    )

    assert focus_command.run_focus(args) == 0
    assert captured["history_path"] == str(history_root / "cli" / "terminal_history")


def test_run_focus_missing_config_launches_inline_setup(monkeypatch) -> None:
    # clear the non-TTY guard so the --rich path proceeds.
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    focus_command = importlib.import_module("openminion.cli.commands.focus")

    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        "openminion.cli.commands.tui._inspect_tui_onboarding",
        lambda args: OnboardingStatus(
            state=OnboardingState.MISSING_CONFIG,
            action=OnboardingAction.LAUNCH_SETUP,
            track=OnboardingTrack.UNKNOWN,
            reason="missing config",
            config_path=_repo_root() / ".openminion" / "agents.json",
            home_root=_repo_root(),
            data_root=_repo_root() / ".openminion",
        ),
    )
    monkeypatch.setattr(
        "openminion.cli.commands.tui._run_inline_setup_for_tui",
        lambda args: 0,
    )

    class _FakeRuntime(SimpleNamespace):
        def close(self) -> None:
            captured["closed"] = True

    class _FakeFocusRuntime:
        def __init__(self, runtime, **kwargs) -> None:
            captured["runtime_kwargs"] = dict(kwargs)

        def set_project_context(self, info) -> None:
            captured["project_context"] = info

    class _FakeApp:
        def __init__(self, **kwargs) -> None:
            captured["app_kwargs"] = dict(kwargs)

        def run(self) -> None:
            return None

    monkeypatch.setattr(
        "openminion.api.runtime.APIRuntime.from_config_path",
        staticmethod(lambda *args, **kwargs: _FakeRuntime()),
    )
    monkeypatch.setattr(
        "openminion.cli.tui.providers.OpenMinionRuntime", _FakeFocusRuntime
    )
    monkeypatch.setattr("openminion.cli.tui.focus.FocusApp", _FakeApp)

    args = SimpleNamespace(
        config=None,
        home_root=str(_repo_root()),
        data_root=None,
        agent="alpha",
        session=None,
        dir="/tmp/focus-onboarding",
        no_interactive=False,
        theme=None,
        # keep this test on the Textual path it patches.
        rich=True,
    )

    assert focus_command.run_focus(args) == 0
    assert captured["runtime_kwargs"]["target"] == "focus"
    assert captured["app_kwargs"]["session"] is None
    assert captured["closed"] is True


def test_run_focus_missing_config_interactive_uses_inline_setup(monkeypatch) -> None:
    # clear the non-TTY guard so the --rich path proceeds.
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    focus_command = importlib.import_module("openminion.cli.commands.focus")

    monkeypatch.setattr(
        "openminion.cli.commands.tui._inspect_tui_onboarding",
        lambda args: OnboardingStatus(
            state=OnboardingState.MISSING_CONFIG,
            action=OnboardingAction.LAUNCH_SETUP,
            track=OnboardingTrack.UNKNOWN,
            reason="missing config",
            config_path=_repo_root() / ".openminion" / "agents.json",
            home_root=_repo_root(),
            data_root=_repo_root() / ".openminion",
        ),
    )
    setup_calls: list[object] = []
    monkeypatch.setattr(
        "openminion.cli.commands.tui._run_inline_setup_for_tui",
        lambda args: setup_calls.append(args) or 0,
    )
    monkeypatch.setattr(
        "openminion.cli.commands.tui._silence_logging_for_tui",
        lambda args: None,
    )

    class _FakeRuntime(SimpleNamespace):
        def close(self) -> None:
            return None

    class _FakeFocusRuntime:
        def __init__(self, runtime, **kwargs) -> None:
            self.kwargs = kwargs

        def set_project_context(self, info) -> None:
            self.project_context = info

    class _FakeApp:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self) -> None:
            return None

    monkeypatch.setattr(
        "openminion.api.runtime.APIRuntime.from_config_path",
        staticmethod(lambda *args, **kwargs: _FakeRuntime()),
    )
    monkeypatch.setattr(
        "openminion.cli.tui.providers.OpenMinionRuntime", _FakeFocusRuntime
    )
    monkeypatch.setattr("openminion.cli.tui.focus.FocusApp", _FakeApp)

    args = SimpleNamespace(
        config=None,
        home_root=str(_repo_root()),
        data_root=None,
        agent="alpha",
        session=None,
        dir="/tmp/focus-onboarding",
        no_interactive=False,
        theme=None,
        # keep this test on the Textual path it patches.
        rich=True,
    )
    assert focus_command.run_focus(args) == 0
    assert len(setup_calls) == 1
