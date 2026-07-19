from __future__ import annotations

from pathlib import Path

import pytest
from rich.markdown import Markdown as RichMarkdown
from rich.text import Text

from openminion.cli.theme import DARK, LIGHT
from openminion.cli.interactive.app import FocusApp, _DemoFocusRuntime
from openminion.cli.presentation.models import (
    ChatMessage,
    MessageKind,
    ToolEvent,
)
from openminion.cli.presentation.tool.blocks import ToolBlockWidget
from openminion.cli.interactive.widgets import FocusTranscript


def _build_diff_widget(theme) -> ToolBlockWidget:
    event = ToolEvent(
        tool_name="file.edit",
        args={"path": "src/foo.py"},
        content="@@ -1,3 +1,3 @@\n-old line\n+new line\n context\n",
        content_type="diff",
    )
    widget = ToolBlockWidget(event, pending=False)
    widget.collapsed = False
    widget._active_theme = lambda: theme  # type: ignore[method-assign]
    return widget


def test_diff_renderer_uses_dark_theme_state_tokens() -> None:
    widget = _build_diff_widget(DARK)
    rendered = widget._render_diff()
    assert isinstance(rendered, Text)
    # Every span carrying the added/removed colors must use the
    # active theme's state_ok / state_error hex, not raw "green"/"red".
    style_strings = {str(span.style) for span in rendered.spans}
    assert any(DARK.state_ok in s for s in style_strings), (
        f"DARK state_ok ({DARK.state_ok}) missing from diff styles: {style_strings}"
    )
    assert any(DARK.state_error in s for s in style_strings), (
        f"DARK state_error ({DARK.state_error}) missing from diff styles: "
        f"{style_strings}"
    )


def test_diff_renderer_uses_light_theme_state_tokens() -> None:
    widget = _build_diff_widget(LIGHT)
    rendered = widget._render_diff()
    style_strings = {str(span.style) for span in rendered.spans}
    assert any(LIGHT.state_ok in s for s in style_strings)
    assert any(LIGHT.state_error in s for s in style_strings)
    assert LIGHT.state_ok != DARK.state_ok
    assert LIGHT.state_error != DARK.state_error


def test_diff_renderer_falls_back_when_theme_unavailable() -> None:
    event = ToolEvent(
        tool_name="file.edit",
        args={"path": "x.py"},
        content="-a\n+b\n",
        content_type="diff",
    )
    widget = ToolBlockWidget(event, pending=False)
    widget.collapsed = False

    def _boom():
        raise RuntimeError("no app context")

    widget._active_theme = _boom  # type: ignore[method-assign]
    rendered = widget._render_diff()
    style_strings = {str(span.style) for span in rendered.spans}
    assert "green" in style_strings
    assert "red" in style_strings


def test_diff_renderer_handles_empty_content() -> None:
    event = ToolEvent(
        tool_name="file.edit",
        args={},
        content="",
        content_type="diff",
    )
    widget = ToolBlockWidget(event, pending=False)
    widget.collapsed = False
    rendered = widget._render_diff()
    assert "(empty diff)" in rendered.plain


@pytest.mark.asyncio
async def test_agent_message_with_code_block_renders_via_markdown(
    tmp_path: Path,
) -> None:
    runtime = _DemoFocusRuntime(working_dir=str(tmp_path), session="codeblock-test")
    app = FocusApp(runtime=runtime, working_dir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.screen.query_one(FocusTranscript)
        chat.push_message(
            ChatMessage(
                kind=MessageKind.AGENT,
                sender="agent",
                body=(
                    "Here is some code:\n\n"
                    "```python\n"
                    "def hello() -> str:\n"
                    "    return 'world'\n"
                    "```\n"
                ),
            )
        )
        await pilot.pause()
        from openminion.cli.interactive.widgets.transcript import FocusMessageWidget

        widgets = list(chat.query(FocusMessageWidget))
        agent_widget = widgets[-1]
        renderable = agent_widget._body_renderable(
            agent_widget._message.body, markdown_allowed=True
        )
        assert isinstance(renderable, RichMarkdown), (
            f"agent code block should render via RichMarkdown, got "
            f"{type(renderable).__name__}"
        )


@pytest.mark.asyncio
async def test_consecutive_same_sender_messages_collapse_header(
    tmp_path: Path,
) -> None:
    runtime = _DemoFocusRuntime(working_dir=str(tmp_path), session="collapse-test")
    app = FocusApp(runtime=runtime, working_dir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.screen.query_one(FocusTranscript)
        chat.push_message(
            ChatMessage(kind=MessageKind.AGENT, sender="agent", body="first")
        )
        chat.push_message(
            ChatMessage(kind=MessageKind.AGENT, sender="agent", body="second")
        )
        await pilot.pause()
        from openminion.cli.interactive.widgets.transcript import FocusMessageWidget

        agent_widgets = [
            w
            for w in chat.query(FocusMessageWidget)
            if w._message.kind == MessageKind.AGENT
        ]
        assert len(agent_widgets) >= 2
        second = agent_widgets[-1]
        assert "--continued" in second.classes, (
            f"second consecutive same-sender widget should carry "
            f"--continued, got classes={list(second.classes)}"
        )
