from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label, Static

from openminion.cli.interactive.widgets import (
    ToolApprovalWidget,
    ToolBlockWidget,
)
from openminion.cli.tui.widgets import ChatMessage
from openminion.cli.tui.widgets.chat import MessageKind, ToolEvent


class _ApprovalHarness(App[None]):
    def __init__(self, widget: ToolApprovalWidget) -> None:
        super().__init__()
        self._widget = widget
        self.events: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        yield self._widget

    def on_tool_approval_widget_approved(
        self, event: ToolApprovalWidget.Approved
    ) -> None:
        self.events.append(("approved", event.tool_name))

    def on_tool_approval_widget_denied(self, event: ToolApprovalWidget.Denied) -> None:
        self.events.append(("denied", event.tool_name))

    def on_tool_approval_widget_allow_all(
        self, event: ToolApprovalWidget.AllowAll
    ) -> None:
        self.events.append(("allow_all", event.tool_name))


class _ToolBlockHarness(App[None]):
    def __init__(self, widget: ToolBlockWidget) -> None:
        super().__init__()
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget


def test_focus_header_shortens_home_relative_paths() -> None:
    from openminion.cli.presentation.header import shorten_working_dir

    raw = str(Path.home() / "repos" / "focus-mode")
    assert shorten_working_dir(raw) == "~/repos/focus-mode"


def test_chat_message_accepts_tool_event() -> None:
    message = ChatMessage(
        kind=MessageKind.TOOL,
        sender="tool:file.read",
        body="tests/test_focus.py",
        tool_event=ToolEvent(
            tool_name="file.read",
            args={"path": "tests/test_focus.py"},
            content="line 1",
        ),
    )

    assert message.tool_event is not None
    assert message.tool_event.tool_name == "file.read"


@pytest.mark.asyncio
async def test_tool_approval_widget_emits_legacy_compat_messages() -> None:
    widget = ToolApprovalWidget("exec.run", {"command": "pwd"}, allow_all=True)
    app = _ApprovalHarness(widget)

    async with app.run_test() as pilot:
        await pilot.pause()
        widget.focus()
        await pilot.press("y")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()

    assert ("approved", "exec.run") in app.events
    assert ("denied", "exec.run") in app.events
    assert ("allow_all", "exec.run") in app.events
    assert app.events.index(("approved", "exec.run")) < app.events.index(
        ("denied", "exec.run")
    )
    assert app.events.index(("denied", "exec.run")) < app.events.index(
        ("allow_all", "exec.run")
    )


def test_tool_approval_widget_args_summary_falls_back_for_unserializable_value() -> (
    None
):
    widget = ToolApprovalWidget("exec.run", {"payload": object()}, allow_all=True)

    summary = widget._args_summary()

    assert "payload" in summary


@pytest.mark.asyncio
async def test_tool_block_widget_renders_exec_output_and_toggles_collapsed() -> None:
    event = ToolEvent(
        tool_name="exec.run",
        args={"command": "pytest -x"},
        content="\n".join(f"line {index}" for index in range(12)),
        duration_ms=300,
        exit_code=0,
    )
    widget = ToolBlockWidget(event)
    app = _ToolBlockHarness(widget)

    async with app.run_test() as pilot:
        await pilot.pause()
        widget.focus()

        title = str(widget.query_one(".focus-tool-block-title", Label).render())
        body = widget.query_one(".focus-tool-block-body", Static)
        assert "Ran" in title
        assert "pytest -x" in title
        assert "<1s" in title
        assert widget.collapsed is True
        assert body.display is False

        await pilot.press("enter")
        await pilot.pause()
        assert widget.collapsed is False
        assert body.display is True
        assert "copy keeps full output" in str(body.render())

        await pilot.press("enter")
        await pilot.pause()
        assert widget.collapsed is True
        assert body.display is False


@pytest.mark.asyncio
async def test_tool_block_widget_renders_diff_and_relative_path_hints() -> None:
    event = ToolEvent(
        tool_name="file.edit",
        args={"path": "tests/test_focus_mode.py"},
        content="@@ -1 +1 @@\n-old\n+new",
        content_type="diff",
    )
    widget = ToolBlockWidget(event)
    app = _ToolBlockHarness(widget)

    async with app.run_test() as pilot:
        await pilot.pause()
        widget.focus()
        await pilot.press("enter")
        await pilot.pause()
        title = str(widget.query_one(".focus-tool-block-title", Label).render())
        body = str(widget.query_one(".focus-tool-block-body", Static).render())

    assert "Edited" in title
    assert "tests/test_focus_mode.py" in title
    assert "@@ -1 +1 @@" in body
    assert "+new" in body
