from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from rich.markdown import Markdown as RichMarkdown
from rich.text import Text
from textual.css.query import QueryError

from openminion.cli.parser.contracts import ProviderBundle
from openminion.cli.presentation import styles
from openminion.cli.status import TokenUsageSnapshot
from openminion.cli.theme import DARK
from openminion.cli.tui.app import DemoSessionsProvider, OpenMinionApp
from openminion.cli.tui.presentation import copy_to_clipboard
from openminion.cli.tui.screen import AppHeader
from openminion.cli.tui.tabs.chat import ChatTab, ThinkingIndicator
from openminion.cli.tui.widgets import SidebarItem
from openminion.cli.tui.widgets.chat import (
    ChatMessage,
    ChatView,
    IdleAnimation,
    MessageContent,
    MessageKind,
    MessageWidget,
)
from textual.widgets import DataTable, Input, Label, OptionList, TextArea


class _ProgressRuntime:
    contract_version = "v1"

    def __init__(self) -> None:
        self._agent_id = "progress-agent"
        self._session_id = "sess-progress"
        self._transport = "demo(progress)"

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def transport(self) -> str:
        return self._transport

    async def send_message(self, text: str, *, progress_callback=None):
        if progress_callback is not None:
            progress_callback({"label": "Planning steps…"})
        await asyncio.sleep(0.05)
        yield f"echo: {text}"

    def get_current_history(self) -> list[ChatMessage]:
        return []

    def list_sessions(self) -> list[SidebarItem]:
        return [SidebarItem(self._session_id, self._session_id, active=True)]

    def list_agents(self) -> list[SidebarItem]:
        return [SidebarItem(self._agent_id, self._agent_id, active=True)]

    def list_tools(self) -> list[tuple[str, bool]]:
        return [("search_brave", True)]

    def switch_session(self, session_id: str) -> list[ChatMessage]:
        self._session_id = session_id
        return []

    def switch_agent(self, agent_id: str) -> None:
        self._agent_id = agent_id

    def new_session(self) -> str:
        self._session_id = "sess-progress-2"
        return self._session_id


class _BridgeRuntime(_ProgressRuntime):
    def __init__(self, *, data_root: str | None = None) -> None:
        super().__init__()
        self._rt = SimpleNamespace(
            config=SimpleNamespace(
                runtime=SimpleNamespace(process_mode="single-process")
            ),
            config_path="/tmp/openminion-test.json",
            data_root=data_root,
        )


class _RetryRuntime(_ProgressRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def send_message(self, text: str, *, progress_callback=None):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("request timed out")
        yield f"echo: {text}"


class _AutoNameSessionsProvider(DemoSessionsProvider):
    def __init__(self) -> None:
        super().__init__()
        self._sessions = [
            {
                "id": "sess-progress",
                "age": "1m",
                "turn_count": 0,
                "agent_id": "progress-agent",
                "channel": "cli",
                "name": "",
            }
        ]
        self.update_calls: list[tuple[str, str]] = []

    def list_all_sessions(self) -> list[dict]:
        return list(self._sessions)

    def update_session_name(self, session_id: str, name: str) -> None:
        self.update_calls.append((session_id, name))
        for session in self._sessions:
            if session["id"] == session_id:
                session["name"] = name


class _UsageRuntime(_ProgressRuntime):
    def __init__(self) -> None:
        super().__init__()
        self._snapshot = TokenUsageSnapshot(
            turn_total_tokens=1500,
            session_total_tokens=4500,
            context_used_tokens=4500,
            context_limit_tokens=200000,
            turn_elapsed_seconds=82.0,
            updated_at_monotonic=100.0,
        )

    def token_usage_snapshot(self) -> TokenUsageSnapshot:
        return self._snapshot


@pytest.fixture(autouse=True)
def _restore_active_theme():
    original_codes = dict(styles._ANSI_CODES)
    original_name = styles.get_active_theme_name()
    styles.set_active_theme(DARK)
    yield
    styles._ANSI_CODES.clear()
    styles._ANSI_CODES.update(original_codes)
    styles._ACTIVE_THEME_NAME = original_name


@pytest.mark.asyncio
async def test_chat_ctrl_a_opens_agent_switch_modal_with_active_preselected() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)
        chat_tab.action_switch_agent()
        await pilot.pause()

        option_list = app.screen.query_one("#agent-switch-list", OptionList)
        assert option_list.highlighted == 0
        app.pop_screen()
        await pilot.pause()


@pytest.mark.asyncio
async def test_chat_agent_switch_modal_confirm_updates_runtime_and_header() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)
        chat_tab.action_switch_agent()
        await pilot.pause()

        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        assert app._runtime.agent_id == "agent-02"
        badge = str(app.screen.query_one(AppHeader).query_one("#header-badge").render())
        assert "agent-02" in badge


@pytest.mark.asyncio
async def test_chat_agent_switch_modal_cancel_keeps_state() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        original_agent = app._runtime.agent_id
        chat_tab = app.screen.query_one(ChatTab)
        chat_tab.action_switch_agent()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app._runtime.agent_id == original_agent


@pytest.mark.asyncio
async def test_chat_agent_streaming_cursor_toggles() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_view = app.screen.query_one(ChatView)
        message = ChatMessage(kind=MessageKind.AGENT, sender="agent", body="")
        widget = chat_view.push_message(message)
        await pilot.pause()

        widget.update_body("partial", streaming=True)
        await pilot.pause()
        body = app.screen.query_one(f"#{message.msg_id}-body")
        assert "▍" in str(body.render())

        widget.update_body("complete", streaming=False)
        await pilot.pause()
        body = app.screen.query_one(f"#{message.msg_id}-body")
        assert "▍" not in str(body.render())


@pytest.mark.asyncio
async def test_dashboard_thinking_indicator_surfaces_elapsed_text() -> None:
    import time

    runtime = _ProgressRuntime()
    bundle = ProviderBundle(sessions=DemoSessionsProvider())
    app = OpenMinionApp(runtime=runtime, providers=bundle)
    async with app.run_test() as pilot:
        await pilot.pause()
        tab = app.screen.query_one(ChatTab)
        indicator = tab.query_one(ThinkingIndicator)

        # Arm a controller via the phase callback (same code path real
        # turns use). The callback registers the controller on the tab
        # for `_tick_elapsed` to consume.
        tab._make_phase_callback()
        controller = tab._active_status_controller
        assert controller is not None, (
            "phase callback should register an active controller"
        )

        # Mark the indicator as thinking so `_tick_elapsed` writes the
        # elapsed slot rather than no-op'ing on idle.
        indicator.is_thinking = True
        await pilot.pause()

        # Rewind the controller's start so elapsed is non-zero — same
        # technique chat CLI parity tests use to avoid real sleeps.
        controller._started_at = time.perf_counter() - 1.5

        # `_tick_elapsed` runs on the event-loop side of the boundary
        # (no `call_from_thread`), so it writes the reactive slot
        # synchronously inside this test.
        tab._tick_elapsed()
        await pilot.pause()

        assert indicator.elapsed_text, (
            f"dashboard ThinkingIndicator must surface elapsed; "
            f"got {indicator.elapsed_text!r}"
        )
        assert "s" in indicator.elapsed_text, (
            f"elapsed format expected to include seconds, "
            f"got {indicator.elapsed_text!r}"
        )

        # When the spinner stops, elapsed text must clear so a stale
        # snapshot does not leak into the next turn's first frame.
        indicator.is_thinking = False
        await pilot.pause()
        tab._tick_elapsed()
        await pilot.pause()
        assert indicator.elapsed_text == "", (
            f"elapsed should clear when thinking ends; got {indicator.elapsed_text!r}"
        )


@pytest.mark.asyncio
async def test_dashboard_token_usage_summary_renders_shared_snapshot() -> None:
    runtime = _UsageRuntime()
    bundle = ProviderBundle(sessions=DemoSessionsProvider())
    app = OpenMinionApp(runtime=runtime, providers=bundle)
    async with app.run_test() as pilot:
        await pilot.pause()
        tab = app.screen.query_one(ChatTab)
        tab._refresh_token_usage_summary()
        await pilot.pause()

        summary = str(app.screen.query_one("#dashboard-token-usage", Label).render())
        assert "turn 1.5k" in summary
        assert "session 4.5k" in summary
        assert "ctx 4.5k / 200k (2%)" in summary
        assert "total 1m 22s" in summary


@pytest.mark.asyncio
async def test_dashboard_clear_keeps_session_token_summary() -> None:
    runtime = _UsageRuntime()
    bundle = ProviderBundle(sessions=DemoSessionsProvider())
    app = OpenMinionApp(runtime=runtime, providers=bundle)
    async with app.run_test() as pilot:
        await pilot.pause()
        tab = app.screen.query_one(ChatTab)
        app.screen.query_one(ChatView).clear_messages()
        tab._refresh_token_usage_summary()
        await pilot.pause()

        summary = str(app.screen.query_one("#dashboard-token-usage", Label).render())
        assert "session 4.5k" in summary


def test_dashboard_tick_elapsed_ignores_missing_indicator_query_error() -> None:
    tab = object.__new__(ChatTab)
    tab._runtime = SimpleNamespace()
    tab._active_status_controller = object()

    def _raise_query_error(*args, **kwargs):
        raise QueryError("missing widget")

    tab.query_one = _raise_query_error
    tab._tick_elapsed()


def test_dashboard_refresh_token_usage_summary_ignores_missing_label_query_error() -> (
    None
):
    tab = object.__new__(ChatTab)
    tab._runtime = SimpleNamespace()

    def _raise_query_error(*args, **kwargs):
        raise QueryError("missing label")

    tab.query_one = _raise_query_error
    tab._refresh_token_usage_summary()


def test_dashboard_set_busy_ignores_missing_widgets_query_error() -> None:
    tab = object.__new__(ChatTab)

    def _raise_query_error(*args, **kwargs):
        raise QueryError("missing widget")

    tab.query_one = _raise_query_error
    tab._set_busy(True)
    assert tab._busy is True


def test_dashboard_cycle_permission_mode_ignores_missing_chat_view_query_error() -> (
    None
):
    tab = object.__new__(ChatTab)
    tab._cycle_permission_mode = lambda: "readonly"

    def _raise_query_error(*args, **kwargs):
        raise QueryError("missing chat view")

    tab.query_one = _raise_query_error
    tab.action_cycle_permission_mode()


def test_idle_animation_uses_breathing_timing_profile() -> None:
    assert IdleAnimation.frame_interval_seconds(0) == 0.2
    assert IdleAnimation.frame_interval_seconds(7) == 0.5
    assert IdleAnimation.frame_interval_seconds(10) == 0.2


@pytest.mark.asyncio
async def test_chat_tool_result_renders_separate_result_block() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_view = app.screen.query_one(ChatView)
        before_dividers = len(app.screen.query("#chat-view .message-tool-divider"))
        before_results = len(app.screen.query("#chat-view .message-tool-result"))
        chat_view.push_message(
            ChatMessage(
                kind=MessageKind.TOOL,
                sender="tool:search_brave",
                body="search(query='python')",
                tool_result="Top result: python.org",
            )
        )
        await pilot.pause()

        assert (
            len(app.screen.query("#chat-view .message-tool-divider"))
            == before_dividers + 1
        )
        assert (
            len(app.screen.query("#chat-view .message-tool-result"))
            == before_results + 1
        )
        rendered_results = [
            str(node.render())
            for node in app.screen.query("#chat-view .message-tool-result")
        ]
        assert any(
            "Top result: python.org" in rendered for rendered in rendered_results
        )


@pytest.mark.asyncio
async def test_tool_message_shows_spinner_until_result_arrives() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_view = app.screen.query_one(ChatView)
        message = ChatMessage(
            kind=MessageKind.TOOL,
            sender="tool:weather",
            body="weather(location='SF')",
        )
        widget = chat_view.push_message(message)
        await pilot.pause()

        spinner = app.screen.query_one(f"#{message.msg_id}-tool-spinner", Label)
        assert spinner.display is True
        assert "tool in progress" in str(spinner.render())

        widget.set_tool_result("13 C")
        await pilot.pause()

        assert spinner.display is False
        assert app.screen.query_one(f"#{message.msg_id}-tool-result")


@pytest.mark.asyncio
async def test_chat_idle_animation_hides_after_first_real_turn() -> None:
    app = OpenMinionApp(runtime=_ProgressRuntime())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        chat_view = app.screen.query_one(ChatView)
        idle = app.screen.query_one("#chat-idle", IdleAnimation)

        assert not idle.has_class("--hidden")
        assert chat_view.bottom_gap >= 0

        chat_view.push_message(
            ChatMessage(kind=MessageKind.USER, sender="you", body="hi")
        )
        await pilot.pause()
        await pilot.pause()

        assert idle.has_class("--hidden")


@pytest.mark.asyncio
async def test_chat_markdown_and_tool_results_use_markdown_rendering() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_view = app.screen.query_one(ChatView)
        agent_message = ChatMessage(
            kind=MessageKind.AGENT,
            sender="agent",
            body="**Hello**\n\n```python\nprint('ok')\n```",
        )
        tool_message = ChatMessage(
            kind=MessageKind.TOOL,
            sender="tool:search_brave",
            body="search(query='python')",
            tool_result='```json\n{"ok": true}\n```',
        )
        chat_view.push_message(agent_message)
        chat_view.push_message(tool_message)
        await pilot.pause()

        agent_body = app.screen.query_one(
            f"#{agent_message.msg_id}-body", MessageContent
        )
        tool_result = app.screen.query_one(
            f"#{tool_message.msg_id}-tool-result", MessageContent
        )

        assert agent_body.markdown_enabled is True
        assert isinstance(agent_body.renderable_value, RichMarkdown)
        assert tool_result.markdown_enabled is True
        assert isinstance(tool_result.renderable_value, RichMarkdown)


@pytest.mark.asyncio
async def test_chat_search_filters_and_highlights_matches_then_escape_restores() -> (
    None
):
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)
        chat_view = app.screen.query_one(ChatView)
        msg1 = ChatMessage(kind=MessageKind.AGENT, sender="agent", body="alpha weather")
        msg2 = ChatMessage(kind=MessageKind.AGENT, sender="agent", body="beta search")
        chat_view.push_message(msg1)
        chat_view.push_message(msg2)
        await pilot.pause()

        chat_tab.action_toggle_search()
        await pilot.pause()
        search = app.screen.query_one("#chat-search-input", Input)
        search.value = "alpha"
        await pilot.pause()

        widgets = {
            widget._message.msg_id: widget for widget in app.screen.query(MessageWidget)
        }
        assert widgets[msg1.msg_id].display is True
        assert widgets[msg2.msg_id].display is False

        body = app.screen.query_one(f"#{msg1.msg_id}-body", MessageContent)
        assert isinstance(body.renderable_value, Text)
        assert body.renderable_value.spans

        await pilot.press("escape")
        await pilot.pause()
        assert widgets[msg2.msg_id].display is True


@pytest.mark.asyncio
async def test_chat_sender_grouping_hides_repeated_headers() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_view = app.screen.query_one(ChatView)
        first = chat_view.push_message(
            ChatMessage(kind=MessageKind.AGENT, sender="agent", body="first")
        )
        second = chat_view.push_message(
            ChatMessage(kind=MessageKind.AGENT, sender="agent", body="second")
        )
        third = chat_view.push_message(
            ChatMessage(kind=MessageKind.USER, sender="you", body="reply")
        )
        await pilot.pause()

        assert not first.has_class("--continued")
        assert second.has_class("--continued")
        assert not third.has_class("--continued")


@pytest.mark.asyncio
async def test_chat_error_rendering_adds_hierarchy_and_retry_hint() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_view = app.screen.query_one(ChatView)
        message = ChatMessage(
            kind=MessageKind.ERROR,
            sender="error",
            body="Top line\nTrace detail",
            retryable_error=True,
        )
        chat_view.push_message(message)
        await pilot.pause()

        body = app.screen.query_one(f"#{message.msg_id}-body", MessageContent)
        assert isinstance(body.renderable_value, Text)
        rendered = body.renderable_value.plain
        assert "Top line" in rendered
        assert "Trace detail" in rendered
        assert "Will retry automatically" in rendered


@pytest.mark.asyncio
async def test_thinking_indicator_shimmers_and_fades() -> None:
    app = OpenMinionApp(runtime=_ProgressRuntime())
    async with app.run_test() as pilot:
        await pilot.pause()
        indicator = app.screen.query_one(ThinkingIndicator)

        indicator.is_thinking = True
        indicator.watch__frame(0)
        await pilot.pause()

        label = app.screen.query_one("#thinking-label", Label)
        renderable = label.render()
        assert "thinking" in str(renderable)
        assert getattr(renderable, "spans", [])
        assert indicator.styles.opacity == 1

        indicator.is_thinking = False
        await pilot.pause()
        assert indicator.styles.opacity == 0


def test_thinking_indicator_ignores_missing_label_widget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    indicator = ThinkingIndicator()
    indicator.is_thinking = True

    def _raise_query_error(*args, **kwargs):
        raise QueryError("missing label")

    monkeypatch.setattr(indicator, "query_one", _raise_query_error)
    indicator.watch__frame(0)


@pytest.mark.asyncio
async def test_chat_view_bottom_gap_disappears_once_history_overflows() -> None:
    app = OpenMinionApp(runtime=_ProgressRuntime())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        chat_view = app.screen.query_one(ChatView)

        assert chat_view.bottom_gap >= 0

        for index in range(24):
            chat_view.push_message(
                ChatMessage(
                    kind=MessageKind.AGENT,
                    sender="agent",
                    body=(f"message {index}\n" * 4).strip(),
                )
            )
        await pilot.pause()
        await pilot.pause()

        assert chat_view.bottom_gap == 0


@pytest.mark.asyncio
async def test_chat_progress_callback_updates_thinking_indicator_label() -> None:
    app = OpenMinionApp(runtime=_ProgressRuntime())
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)
        indicator = app.screen.query_one(ThinkingIndicator)

        chat_tab._send_message("show progress")

        for _ in range(6):
            await pilot.pause()
            if indicator.status_label == "Planning steps…":
                break

        assert indicator.status_label == "Planning steps…"
        for _ in range(4):
            await pilot.pause()


@pytest.mark.asyncio
async def test_chat_bridge_command_routes_cli_output_into_chat(monkeypatch) -> None:
    from openminion.cli.chat.commands import base as base_module

    seen: list[str] = []

    def _fake_handle_chat_command(**kwargs):
        seen.append(kwargs["line"])
        print("grant-001 tool=exec")
        return base_module.ChatCommandResult(handled=True)

    monkeypatch.setattr(
        "openminion.cli.chat.commands.handle_chat_command",
        _fake_handle_chat_command,
    )

    app = OpenMinionApp(runtime=_BridgeRuntime())
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)
        chat_tab._handle_command("/grants")
        await pilot.pause()

        assert seen == ["/grants"]
        system_messages = [
            str(node.render())
            for node in app.screen.query("#chat-view .message-system")
        ]
        assert any("grant-001 tool=exec" in rendered for rendered in system_messages)


@pytest.mark.asyncio
async def test_trust_modal_dispatches_selected_categories() -> None:
    seen: list[str] = []

    app = OpenMinionApp(runtime=_BridgeRuntime())
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)
        chat_tab._handle_cli_bridge_command = seen.append  # type: ignore[method-assign]

        chat_tab._open_trust_modal()
        await pilot.pause()

        app.screen.query_one("#trust-cat-exec").press()
        app.screen.query_one("#trust-cat-weather").press()
        await pilot.pause()
        app.screen.query_one("#trust-confirm").press()
        await pilot.pause()

        assert seen == ["/trust exec", "/trust weather"]


@pytest.mark.asyncio
async def test_artifacts_modal_renders_last_turn_artifacts_table() -> None:
    app = OpenMinionApp(runtime=_BridgeRuntime())
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)
        chat_tab._last_artifacts = [
            {"name": "plan.json", "type": "json", "size": "120 B"},
            {"name": "trace.txt", "type": "text", "size": "2 KB"},
        ]

        chat_tab._open_artifacts_modal()
        await pilot.pause()

        table = app.screen.query_one("#artifacts-table", DataTable)
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_chat_debug_command_uses_cli_bridge_output_when_available(
    monkeypatch,
) -> None:
    from openminion.cli.chat.commands import base as base_module

    seen: list[str] = []

    def _fake_handle_chat_command(**kwargs):
        seen.append(kwargs["line"])
        print("debug context")
        print("tool_calls=3")
        return base_module.ChatCommandResult(handled=True)

    monkeypatch.setattr(
        "openminion.cli.chat.commands.handle_chat_command",
        _fake_handle_chat_command,
    )

    app = OpenMinionApp(runtime=_BridgeRuntime())
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)
        chat_tab._handle_command("/debug")
        await pilot.pause()

        assert seen == ["/debug"]
        system_messages = [
            str(node.render())
            for node in app.screen.query("#chat-view .message-system")
        ]
        assert any("debug context" in rendered for rendered in system_messages)
        assert any("tool_calls=3" in rendered for rendered in system_messages)


@pytest.mark.asyncio
async def test_chat_theme_command_renders_theme_info_in_system_message() -> None:
    app = OpenMinionApp(runtime=_BridgeRuntime())
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)
        chat_tab._handle_command("/theme")
        await pilot.pause()

        system_messages = [
            str(node.render())
            for node in app.screen.query("#chat-view .message-system")
        ]
        assert any("Chat Theme Settings" in rendered for rendered in system_messages)
        assert any("NO_COLOR env" in rendered for rendered in system_messages)


@pytest.mark.asyncio
async def test_chat_theme_list_subcommand_renders_available_themes() -> None:
    app = OpenMinionApp(runtime=_BridgeRuntime())
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)
        chat_tab._handle_command("/theme list")
        await pilot.pause()

        system_messages = [
            str(node.render())
            for node in app.screen.query("#chat-view .message-system")
        ]
        assert any("Available themes" in rendered for rendered in system_messages)
        assert any(
            "dark" in rendered and "light" in rendered for rendered in system_messages
        )


@pytest.mark.asyncio
async def test_chat_theme_switch_subcommand_applies_dashboard_theme() -> None:
    app = OpenMinionApp(runtime=_BridgeRuntime())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.active_theme.name == "dark"
        chat_tab = app.screen.query_one(ChatTab)
        chat_tab._handle_command("/theme light")
        await pilot.pause()

        assert app.active_theme.name == "light"
        system_messages = [
            str(node.render())
            for node in app.screen.query("#chat-view .message-system")
        ]
        assert any("session-local" in rendered for rendered in system_messages)


@pytest.mark.asyncio
async def test_chat_theme_save_subcommand_persists_to_data_root(
    tmp_path,
) -> None:
    app = OpenMinionApp(runtime=_BridgeRuntime(data_root=str(tmp_path)))
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)
        chat_tab._handle_command("/theme save light")
        await pilot.pause()

        assert app.active_theme.name == "light"
        persisted = tmp_path / "cli" / "theme.json"
        assert persisted.exists()
        system_messages = [
            str(node.render())
            for node in app.screen.query("#chat-view .message-system")
        ]
        assert any("theme saved to" in rendered.lower() for rendered in system_messages)


@pytest.mark.asyncio
async def test_chat_menu_command_renders_grouped_menu_in_system_message() -> None:
    app = OpenMinionApp(runtime=_BridgeRuntime())
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)
        chat_tab._handle_command("/menu")
        await pilot.pause()

        system_messages = [
            str(node.render())
            for node in app.screen.query("#chat-view .message-system")
        ]
        assert any("=== SESSION ===" in rendered for rendered in system_messages)
        assert any("=== CONTROL ===" in rendered for rendered in system_messages)


@pytest.mark.asyncio
async def test_chat_mcp_command_renders_status_report() -> None:
    class _MCPRuntime(_BridgeRuntime):
        def mcp_status_report(self) -> str:
            return "MCP servers:\n- fixture  [ready]  transport=stdio  tools=1"

    app = OpenMinionApp(runtime=_MCPRuntime())
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)
        chat_tab._handle_command("/mcp")
        await pilot.pause()

        system_messages = [
            str(node.render())
            for node in app.screen.query("#chat-view .message-system")
        ]
        assert any("MCP servers:" in rendered for rendered in system_messages)
        assert any("fixture" in rendered for rendered in system_messages)


def test_copy_to_clipboard_uses_platform_clipboard_command(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/pbcopy")

    def _fake_run(cmd, *, input=None, check=None, timeout=None):
        captured["cmd"] = cmd
        captured["input"] = input
        captured["check"] = check
        captured["timeout"] = timeout
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("subprocess.run", _fake_run)

    assert copy_to_clipboard("copied text") is True
    assert captured["cmd"] == ["pbcopy"]
    assert captured["input"] == b"copied text"


@pytest.mark.asyncio
async def test_chat_copy_message_reports_clipboard_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        "openminion.cli.tui.tabs.chat.copy_to_clipboard",
        lambda text: False,
    )

    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_view = app.screen.query_one(ChatView)
        chat_view.push_message(
            ChatMessage(
                kind=MessageKind.AGENT,
                sender="agent",
                body="clipboard me",
            )
        )
        await pilot.pause()

        await pilot.press("ctrl+y")
        await pilot.pause()

        system_messages = [
            str(node.render())
            for node in app.screen.query("#chat-view .message-system")
        ]
        assert any(
            "Clipboard not available" in rendered for rendered in system_messages
        )


@pytest.mark.asyncio
async def test_chat_turn_retries_retryable_errors_and_reports_notice() -> None:
    runtime = _RetryRuntime()
    app = OpenMinionApp(runtime=runtime)
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)
        chat_tab._send_message("retry me")

        for _ in range(20):
            await pilot.pause()

        assert runtime.calls == 2
        system_messages = [
            str(node.render())
            for node in app.screen.query("#chat-view .message-system")
        ]
        assert any(
            "transient failure, retrying" in rendered for rendered in system_messages
        )
        rendered_bodies = [
            str(node.render()) for node in app.screen.query("#chat-view .message-body")
        ]
        assert any("echo: retry me" in rendered for rendered in rendered_bodies)


@pytest.mark.asyncio
async def test_chat_diff_command_surfaces_git_diff_output(monkeypatch) -> None:

    def _fake_render_git_diff(_working_dir, _args=""):
        return type(
            "Result",
            (),
            {"display_body": "diff --git a/note.txt b/note.txt\n+new"},
        )()

    monkeypatch.setattr(
        "openminion.cli.tui.presentation.git.diff.render_git_diff",
        _fake_render_git_diff,
    )
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)

        chat_tab._handle_command("/diff note.txt")
        await pilot.pause()

        system_messages = [
            str(node.render())
            for node in app.screen.query("#chat-view .message-system")
        ]
        assert any("diff --git" in rendered for rendered in system_messages)
        assert any("+new" in rendered for rendered in system_messages)


@pytest.mark.asyncio
async def test_chat_auto_names_new_session_after_first_agent_response() -> None:
    runtime = _ProgressRuntime()
    sessions_provider = _AutoNameSessionsProvider()
    app = OpenMinionApp(
        runtime=runtime,
        providers=ProviderBundle(sessions=sessions_provider),
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_tab = app.screen.query_one(ChatTab)
        chat_tab._send_message("name me")

        for _ in range(10):
            await pilot.pause()

        assert sessions_provider.update_calls == [("sess-progress", "echo: name me")]


@pytest.mark.asyncio
async def test_sidebar_session_preview_appears_after_focus_delay() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        session_item = next(iter(app.screen.query("#sidebar .sidebar-item")))
        session_item.focus()
        await asyncio.sleep(0.35)
        await pilot.pause()

        preview = app.screen.query_one(ChatTab).query_one("#sidebar-preview")
        assert not preview.has_class("--hidden")
        assert (
            "you:" in str(preview.render()).lower()
            or "agent:" in str(preview.render()).lower()
        )


@pytest.mark.asyncio
async def test_chat_multiline_toggle_uses_text_area_and_submits() -> None:
    app = OpenMinionApp(runtime=_ProgressRuntime())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+l")
        await pilot.pause()

        editor = app.screen.query_one("#message-editor", TextArea)
        assert not editor.has_class("--hidden")

        editor.text = "first line\nsecond line"
        editor.action_submit()
        for _ in range(4):
            await pilot.pause()

        rendered_messages = [
            str(node.render()) for node in app.screen.query("#chat-view .message-body")
        ]
        assert any("first line" in rendered for rendered in rendered_messages)
