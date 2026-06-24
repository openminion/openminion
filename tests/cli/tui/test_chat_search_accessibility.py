from __future__ import annotations

import pytest
from rich.text import Text
from textual.app import App, ComposeResult

from openminion.cli.tui.widgets.chat import (
    ChatMessage,
    MessageContent,
    MessageKind,
    MessageWidget,
)


class _ChatHarness(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.widget = MessageWidget(
            ChatMessage(
                kind=MessageKind.AGENT,
                sender="agent",
                body="alpha weather",
            )
        )

    def compose(self) -> ComposeResult:
        yield self.widget


def test_highlight_text_adds_non_color_marker_without_mutating_plain_text() -> None:
    rendered = MessageWidget._highlight_text("alpha weather alpha", "alpha")

    assert isinstance(rendered, Text)
    assert rendered.plain == "alpha weather alpha"
    assert rendered.spans
    style_strings = {str(span.style) for span in rendered.spans}
    assert any("underline" in style for style in style_strings), style_strings
    assert any("rgb(255,215,0)" in style for style in style_strings), style_strings


@pytest.mark.asyncio
async def test_chat_view_search_rendering_keeps_accessible_match_cue() -> None:
    app = _ChatHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.widget.set_search_query("alpha")
        await pilot.pause()

        body = app.screen.query_one(
            f"#{app.widget._message.msg_id}-body", MessageContent
        )
        assert isinstance(body.renderable_value, Text)
        assert body.renderable_value.plain == "alpha weather"
        style_strings = {str(span.style) for span in body.renderable_value.spans}
        assert any("underline" in style for style in style_strings), style_strings
        assert any("rgb(255,215,0)" in style for style in style_strings), style_strings
