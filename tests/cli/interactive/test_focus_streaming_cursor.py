from __future__ import annotations

from pathlib import Path

import pytest

from openminion.cli.interactive.app import FocusApp, _DemoFocusRuntime
from openminion.cli.interactive.widgets import FocusTranscript
from openminion.cli.interactive.widgets.transcript import ChatMessage, MessageKind


def _make_app(tmp: str) -> FocusApp:
    runtime = _DemoFocusRuntime(working_dir=tmp)
    return FocusApp(runtime=runtime, working_dir=tmp)


@pytest.mark.asyncio
async def test_focus_streaming_cursor_renders_then_clears(tmp_path: Path) -> None:
    app = _make_app(str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.screen.query_one(FocusTranscript)

        message = ChatMessage(kind=MessageKind.AGENT, sender="agent", body="")
        widget = chat.push_message(message)
        await pilot.pause()

        widget.update_body("partial reply", streaming=True)
        await pilot.pause()
        body = app.screen.query_one(f"#{widget.id}-body")
        assert "▍" in str(body.render()), (
            "focus streaming should render the `▍` cursor — parity "
            "with dashboard test_chat_agent_streaming_cursor_toggles"
        )

        widget.update_body("complete reply", streaming=False)
        await pilot.pause()
        body = app.screen.query_one(f"#{widget.id}-body")
        assert "▍" not in str(body.render()), (
            "cursor must clear when streaming finishes"
        )
