from __future__ import annotations

import asyncio
import tempfile

import pytest
from textual.app import App, ComposeResult

from openminion.cli.presentation import styles
from openminion.cli.theme import DARK
from openminion.cli.interactive.app import FocusApp, _DemoFocusRuntime
from openminion.cli.interactive.widgets.approval import ToolApprovalWidget


@pytest.fixture(autouse=True)
def _restore_active_theme():
    original_codes = dict(styles._ANSI_CODES)
    original_name = styles.get_active_theme_name()
    styles.set_active_theme(DARK)
    yield
    styles._ANSI_CODES.clear()
    styles._ANSI_CODES.update(original_codes)
    styles._ACTIVE_THEME_NAME = original_name


class _ApprovalHarness(App):
    def __init__(self, widget: ToolApprovalWidget) -> None:
        super().__init__()
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget


@pytest.mark.asyncio
async def test_widget_renders_three_options_when_allow_all_true() -> None:
    widget = ToolApprovalWidget("exec.run", {"command": "ls"}, allow_all=True)
    async with _ApprovalHarness(widget).run_test() as pilot:
        await pilot.pause()
        labels = [str(child.render()) for child in widget.children]
    blob = " ".join(labels)
    assert "[A] Allow once" in blob
    assert "[S] Session allow" in blob
    assert "[D] Deny" in blob


@pytest.mark.asyncio
async def test_widget_renders_two_options_when_allow_all_false() -> None:
    widget = ToolApprovalWidget("exec.run", {"command": "ls"}, allow_all=False)
    async with _ApprovalHarness(widget).run_test() as pilot:
        await pilot.pause()
        labels = [str(child.render()) for child in widget.children]
    blob = " ".join(labels)
    assert "[A] Allow once" in blob
    assert "[D] Deny" in blob
    assert "[S]" not in blob, "session option must not appear when allow_all=False"


class _CapturingApp(App):
    def __init__(self, widget: ToolApprovalWidget) -> None:
        super().__init__()
        self._widget = widget
        self.events: list[tuple[str, dict]] = []

    def compose(self) -> ComposeResult:
        yield self._widget

    def on_tool_approval_widget_approved(
        self, event: ToolApprovalWidget.Approved
    ) -> None:
        self.events.append(
            ("approved", {"scope": event.scope, "tool": event.tool_name})
        )

    def on_tool_approval_widget_denied(self, event: ToolApprovalWidget.Denied) -> None:
        self.events.append(("denied", {"tool": event.tool_name}))

    def on_tool_approval_widget_allow_all(
        self, event: ToolApprovalWidget.AllowAll
    ) -> None:
        self.events.append(("allow_all", {"tool": event.tool_name}))


@pytest.mark.asyncio
async def test_a_keypress_emits_approved_once() -> None:
    widget = ToolApprovalWidget("exec.run", {}, allow_all=True)
    app = _CapturingApp(widget)
    async with app.run_test() as pilot:
        await pilot.pause()
        widget.focus()
        await pilot.press("a")
        await pilot.pause()
    approved = [e for e in app.events if e[0] == "approved"]
    assert approved, "expected Approved event"
    assert approved[0][1]["scope"] == "once"


@pytest.mark.asyncio
async def test_s_keypress_emits_session_approval_and_allow_all() -> None:
    widget = ToolApprovalWidget("exec.run", {}, allow_all=True)
    app = _CapturingApp(widget)
    async with app.run_test() as pilot:
        await pilot.pause()
        widget.focus()
        await pilot.press("s")
        await pilot.pause()
    scopes = [e[1]["scope"] for e in app.events if e[0] == "approved"]
    assert "session" in scopes
    assert any(e[0] == "allow_all" for e in app.events)


@pytest.mark.asyncio
async def test_d_keypress_emits_denied() -> None:
    widget = ToolApprovalWidget("exec.run", {}, allow_all=True)
    app = _CapturingApp(widget)
    async with app.run_test() as pilot:
        await pilot.pause()
        widget.focus()
        await pilot.press("d")
        await pilot.pause()
    assert any(e[0] == "denied" for e in app.events)


@pytest.mark.asyncio
async def test_escape_keypress_defaults_to_deny() -> None:
    widget = ToolApprovalWidget("exec.run", {}, allow_all=True)
    app = _CapturingApp(widget)
    async with app.run_test() as pilot:
        await pilot.pause()
        widget.focus()
        await pilot.press("escape")
        await pilot.pause()
    assert any(e[0] == "denied" for e in app.events)


@pytest.mark.asyncio
async def test_legacy_y_and_enter_alias_to_once_allow() -> None:
    for key in ("y", "enter"):
        widget = ToolApprovalWidget("exec.run", {}, allow_all=True)
        app = _CapturingApp(widget)
        async with app.run_test() as pilot:
            await pilot.pause()
            widget.focus()
            await pilot.press(key)
            await pilot.pause()
        approved = [e for e in app.events if e[0] == "approved"]
        assert approved, f"key {key} should produce Approved"
        assert approved[0][1]["scope"] == "once"


@pytest.mark.asyncio
async def test_s_falls_back_to_once_when_allow_all_false() -> None:
    widget = ToolApprovalWidget("exec.run", {}, allow_all=False)
    app = _CapturingApp(widget)
    async with app.run_test() as pilot:
        await pilot.pause()
        widget.focus()
        await pilot.press("s")
        await pilot.pause()
    approved = [e for e in app.events if e[0] == "approved"]
    assert approved, "S keypress must still produce Approved when session unavailable"
    assert approved[0][1]["scope"] == "once"


@pytest.mark.asyncio
async def test_session_grant_skips_widget_on_second_call() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp, session="grant-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            screen._session_grants.add("file.write")
            result = await screen._approval_callback(
                "file.write", {"path": "/tmp/x"}, call_id="test-1"
            )
            assert result is True
            assert screen._approval_widget is None


@pytest.mark.asyncio
async def test_focus_session_approval_key_adds_session_grant() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp, session="grant-key-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            task = asyncio.create_task(
                screen._approval_callback(
                    "file.write", {"path": "/tmp/x"}, call_id="test-session"
                )
            )
            await pilot.pause()
            assert screen._approval_widget is not None
            screen._approval_widget.focus()
            await pilot.press("s")
            result = await task
            assert result is True
            assert "file.write" in screen._session_grants
