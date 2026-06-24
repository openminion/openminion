from __future__ import annotations

import asyncio

import pytest

from openminion.cli.tui.app import DemoRuntime, OpenMinionApp
from openminion.cli.tui.focus.app import FocusApp, _DemoFocusRuntime
from openminion.cli.tui.presentation import (
    ChatMessage,
    MessageKind,
    ThinkingIndicator,
    ToolBlockWidget,
    ToolEvent,
)
from openminion.cli.tui.widgets import ChatView


@pytest.mark.asyncio
async def test_dashboard_tui_launches_and_mounts_shared_presentation() -> None:
    app = OpenMinionApp(runtime=DemoRuntime())
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_views = list(app.screen.query(ChatView))
        assert chat_views, "dashboard shell did not mount the shared ChatView"
        indicators = list(app.screen.query(ThinkingIndicator))
        assert indicators, "dashboard shell did not mount the shared ThinkingIndicator"


@pytest.mark.asyncio
async def test_dashboard_tool_event_message_renders_shared_tool_block() -> None:
    app = OpenMinionApp(runtime=DemoRuntime())
    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.screen.query_one(ChatView)
        event = ToolEvent(
            tool_name="exec.run",
            args={"command": "pytest -x"},
            content="1 passed\n",
        )
        chat.push_message(
            ChatMessage(
                kind=MessageKind.TOOL,
                sender="tool:exec.run",
                body="pytest -x",
                tool_event=event,
            )
        )
        await pilot.pause()
        blocks = list(app.screen.query(ToolBlockWidget))
        assert blocks, (
            "dashboard did not mount the shared ToolBlockWidget for a "
            "ChatMessage with a ToolEvent"
        )


@pytest.mark.asyncio
async def test_focus_tui_launches_with_demo_shell(
    tmp_path,
) -> None:
    from openminion.cli.tui.focus.widgets import FocusTranscript

    runtime = _DemoFocusRuntime(working_dir=str(tmp_path))
    app = FocusApp(runtime=runtime, working_dir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        focus_transcripts = list(app.screen.query(FocusTranscript))
        assert focus_transcripts, "focus shell did not mount FocusTranscript"
        chat_views = list(app.screen.query(ChatView))
        assert not chat_views, (
            "focus shell must NOT mount ChatView — that is the dashboard's "
            "body widget. Per spec §4 anti-shared-widget boundary, focus "
            "owns FocusTranscript instead."
        )
        assert runtime.is_bound


@pytest.mark.asyncio
async def test_focus_on_mount_uses_shared_runtime_bind_flow(tmp_path) -> None:
    calls: list[str] = []

    from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION

    class _SharedRuntimeStub:
        contract_version = CLI_INTERFACE_VERSION
        agent_id = "openminion"
        session_id = ""
        transport = "gateway"

        def __init__(self, working_dir: str) -> None:
            self._working_dir = working_dir
            self._bound = False

        @property
        def is_bound(self) -> bool:
            return self._bound

        @property
        def working_dir(self) -> str:
            return self._working_dir

        @property
        def provider_name(self) -> str:
            return "stub"

        @property
        def model_name(self) -> str:
            return "stub"

        def find_candidate_session(self):
            calls.append("find_candidate_session")
            return None

        def bind_session(self, session_id: str) -> None:
            calls.append(f"bind_session:{session_id}")
            self._bound = True

        def create_new_session(self) -> str:
            calls.append("create_new_session")
            self._bound = True
            return "focus-sess-stub"

        def new_session(self) -> str:
            return self.create_new_session()

        def switch_session(self, session_id: str) -> list:
            self.bind_session(session_id)
            return []

        def switch_agent(self, agent_id: str) -> None:
            del agent_id

        def get_current_history(self):
            return []

        def list_tools(self):
            return []

        def list_sessions(self):
            return []

        def list_agents(self):
            return []

        async def send_message(self, text, **kwargs):
            del text, kwargs
            yield ""

    stub = _SharedRuntimeStub(working_dir=str(tmp_path))
    app = FocusApp(runtime=stub, working_dir=str(tmp_path))
    async with app.run_test() as pilot:
        for _ in range(20):
            await pilot.pause()
            if stub.is_bound:
                break
        assert "find_candidate_session" in calls, (
            "FocusScreen.on_mount did not call the shared runtime's "
            "find_candidate_session(); bind flow is bypassed."
        )
        assert "create_new_session" in calls, (
            "FocusScreen.on_mount did not call create_new_session() when no "
            "candidate existed; fresh-session path via shared runtime is not wired."
        )
        assert stub.is_bound, "shared runtime was never bound via the mount flow"


@pytest.mark.asyncio
async def test_focus_does_not_create_orphan_session_when_resuming(tmp_path) -> None:
    from types import SimpleNamespace

    from openminion.cli.tui.providers.runtime import OpenMinionRuntime

    stub_rt = SimpleNamespace(
        config=SimpleNamespace(
            agent=SimpleNamespace(name="openminion", default_channel="cli")
        )
    )

    runtime = OpenMinionRuntime(
        stub_rt,
        target="focus",
        bind_immediately=False,
        working_dir=str(tmp_path),
    )
    assert not runtime.is_bound
    assert runtime.session_id == ""


@pytest.mark.asyncio
async def test_focus_inline_approval_fires_shared_callback(tmp_path) -> None:
    from openminion.cli.tui.focus.widgets import ToolApprovalWidget

    runtime = _DemoFocusRuntime(working_dir=str(tmp_path))
    app = FocusApp(runtime=runtime, working_dir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        approval_task = asyncio.create_task(
            screen._approval_callback("exec.run", {"command": "ls"}, None)
        )
        for _ in range(10):
            await pilot.pause()
            widgets = list(app.screen.query(ToolApprovalWidget))
            if widgets:
                break
        assert widgets, (
            "focus inline approval did not mount the shared ToolApprovalWidget"
        )
        if screen._approval_future is not None:
            screen._approval_future.set_result("approve")
        result = await approval_task
        assert result is True, "approval callback did not return True on [Y]"
