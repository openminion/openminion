from __future__ import annotations

import pytest

from openminion.cli.tui.app import OpenMinionApp
from openminion.cli.tui.tabs.chat import ChatTab
from openminion.cli.tui.tabs.tasks import TasksTab
from openminion.cli.tui.screen import AppHeader
from openminion.cli.tui.widgets.chat import ChatView, MessageKind
from openminion.cli.tui.widgets.input_bar import ChatInputBar
from openminion.cli.parser.contracts import ProviderBundle
from textual.widgets import Input


# ── AC-1: /clear removes all messages and stays clear on recompose ────────────


@pytest.mark.asyncio
async def test_clear_removes_messages_and_does_not_restore_on_recompose() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)
        chat_view = app.screen.query_one(ChatView)

        # There should be at least one initial message from DemoRuntime
        initial_count = len(chat_view._messages)
        assert initial_count > 0

        chat_tab._handle_command("/clear")
        await pilot.pause()

        assert len(chat_view._messages) == 0

        # Trigger recompose — messages should NOT come back
        await chat_view.recompose()
        await pilot.pause()
        assert len(chat_view._messages) == 0


# ── AC-2: /new creates a new session; sidebar session count increases ─────────


@pytest.mark.asyncio
async def test_new_session_refreshes_sidebar() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)

        before = len(app._runtime.list_sessions())
        chat_tab.action_new_session()
        await pilot.pause()

        after = len(app._runtime.list_sessions())
        assert after > before


# ── AC-3: busy-state notice appears; no crash ─────────────────────────────────


@pytest.mark.asyncio
async def test_busy_state_notice_when_sending_while_busy() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)
        chat_view = app.screen.query_one(ChatView)

        chat_tab._busy = True
        chat_tab._send_message("hello")
        await pilot.pause()

        system_msgs = [
            m
            for m in chat_view._messages
            if m.kind == MessageKind.SYSTEM and "working" in m.body.lower()
        ]
        assert system_msgs, "expected a SYSTEM busy notice"
        chat_tab._busy = False


# ── AC-4: agent switch failure shows ERROR message; TUI stays alive ───────────


@pytest.mark.asyncio
async def test_agent_switch_failure_shows_error_message() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)
        chat_view = app.screen.query_one(ChatView)

        before = len(chat_view._messages)

        def _raise(agent_id):
            raise RuntimeError("no such agent")

        original = app._runtime.switch_agent
        app._runtime.switch_agent = _raise
        try:
            chat_tab._do_switch_agent("bad-agent")
            await pilot.pause()
        finally:
            app._runtime.switch_agent = original

        error_msgs = [m for m in chat_view._messages if m.kind == MessageKind.ERROR]
        assert error_msgs, "expected an ERROR message after failed agent switch"
        assert len(chat_view._messages) > before


# ── AC-5: on_mount history error shows ERROR; tab renders ────────────────────


@pytest.mark.asyncio
async def test_on_mount_history_error_shows_error_message() -> None:
    from openminion.cli.tui.app import DemoRuntime
    from openminion.cli.parser.contracts import ProviderBundle

    runtime = DemoRuntime()

    def _raise():
        raise RuntimeError("history unavailable")

    runtime.get_current_history = _raise

    app = OpenMinionApp(runtime=runtime, providers=ProviderBundle.all_demo())
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_view = app.screen.query_one(ChatView)
        error_msgs = [m for m in chat_view._messages if m.kind == MessageKind.ERROR]
        assert error_msgs, "expected ERROR message when get_current_history raises"


@pytest.mark.asyncio
async def test_demo_runtime_does_not_fake_keyword_tool_chunks() -> None:
    from openminion.cli.tui.app import DemoRuntime

    runtime = DemoRuntime()
    chunks = [chunk async for chunk in runtime.send_message("search for docs")]

    assert chunks
    assert all("[tool:search_brave]" not in chunk for chunk in chunks)
    assert all("Searching for:" not in chunk for chunk in chunks)


# ── AC-6: input history ↑ cycles through sent messages ───────────────────────


@pytest.mark.asyncio
async def test_input_history_up_key_cycles_messages() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        input_bar = app.screen.query_one(ChatInputBar)
        inp = input_bar.query_one("#message-input", Input)

        # Seed history directly (append order: oldest → newest)
        input_bar._history = ["first", "second", "third"]
        input_bar._history_pos = -1
        input_bar._draft = ""

        # ↑ starts from most recent (end of list)
        input_bar._history_back(inp)
        assert inp.value == "third"

        input_bar._history_back(inp)
        assert inp.value == "second"

        input_bar._history_back(inp)
        assert inp.value == "first"

        # ↓ back toward draft
        input_bar._history_forward(inp)
        input_bar._history_forward(inp)
        input_bar._history_forward(inp)
        assert inp.value == ""


# ── AC-7: session switch failure shows ERROR; TUI stays alive ────────────────


@pytest.mark.asyncio
async def test_session_switch_failure_shows_error_message() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)
        chat_view = app.screen.query_one(ChatView)

        original = app._runtime.switch_session
        app._runtime.switch_session = lambda _: (_ for _ in ()).throw(
            RuntimeError("db error")
        )
        try:
            chat_tab._do_switch_session("bad-session")
            await pilot.pause()
        finally:
            app._runtime.switch_session = original

        error_msgs = [m for m in chat_view._messages if m.kind == MessageKind.ERROR]
        assert error_msgs, "expected ERROR message after failed session switch"


# ── Empty-tab notice when provider is None ────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_tab_notice_shown_when_provider_is_none() -> None:
    from openminion.cli.tui.app import DemoRuntime

    app = OpenMinionApp(
        runtime=DemoRuntime(),
        providers=ProviderBundle(),  # all providers None
    )
    async with app.run_test() as pilot:
        await pilot.pause()

        # Tasks tab
        await pilot.press("ctrl+2")
        await pilot.pause()
        tasks_tab = app.screen.query_one(TasksTab)
        notices = tasks_tab.query(".tab-empty-notice")
        assert len(notices) > 0, (
            "TasksTab should show empty notice when provider is None"
        )


# ── DEMO badge present in demo mode ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_demo_badge_visible_in_demo_mode() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        header = app.screen.query_one(AppHeader)
        badge = header.query("#header-demo-badge")
        assert len(badge) > 0, "DEMO badge should be visible in demo mode"


# ── DEMO badge absent in non-demo transport ───────────────────────────────────


@pytest.mark.asyncio
async def test_demo_badge_absent_when_transport_is_not_demo() -> None:
    from openminion.cli.tui.app import DemoRuntime

    runtime = DemoRuntime()
    runtime._transport = "gateway"  # override transport to non-demo value

    app = OpenMinionApp(runtime=runtime, providers=ProviderBundle.all_demo())
    async with app.run_test() as pilot:
        await pilot.pause()
        header = app.screen.query_one(AppHeader)
        badge = header.query("#header-demo-badge")
        assert len(badge) == 0, (
            "DEMO badge should not appear when transport is not demo"
        )
