from __future__ import annotations

import asyncio
import io

import pyte
from rich.console import Console

from openminion.cli.interactive.terminal.shell import _run_agent_turn
from openminion.cli.interactive.terminal.transcript import TerminalTranscript
from openminion.cli.presentation.models import ChatMessage, MessageKind


class _StubRuntime:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def send_message(self, text, *, progress_callback=None):
        del text, progress_callback
        yield self._reply


def _capture_terminal_output(callback) -> str:
    buffer = io.BytesIO()

    # Build a Console that writes raw bytes to our buffer (so
    # ANSI escapes are preserved for pyte).
    text_writer = io.TextIOWrapper(buffer, encoding="utf-8", write_through=True)
    console = Console(
        file=text_writer,
        force_terminal=True,
        force_interactive=False,
        width=120,
        height=40,
        color_system=None,  # avoid color escapes muddying assertions
    )
    callback(console)
    text_writer.flush()
    text_writer.detach()

    raw_bytes = buffer.getvalue()

    # Sanity check: alt-screen enable sequence MUST NOT appear.
    assert b"\x1b[?1049h" not in raw_bytes, (
        "terminal-flow shell emitted the alt-screen-enable escape "
        "sequence — the spec §4 boundary is violated"
    )

    # Feed through pyte to render into a screen grid.
    screen = pyte.Screen(120, 40)
    stream = pyte.Stream(screen)
    stream.feed(raw_bytes.decode("utf-8", errors="replace"))
    return "\n".join(screen.display)


def test_streaming_turn_lands_in_pyte_screen_post_exit() -> None:

    def _run(console):
        transcript = TerminalTranscript(console)
        # Push the user message.
        transcript.push_message(
            ChatMessage(kind=MessageKind.USER, sender="you", body="hello world")
        )
        # Run an agent turn.
        runtime = _StubRuntime("the assistant response")
        asyncio.run(
            _run_agent_turn(
                text="hello world",
                runtime=runtime,
                transcript=transcript,
                status_line=None,
            )
        )

    screen_contents = _capture_terminal_output(_run)
    # Both bodies must be visible in the captured screen — the
    # load-bearing assertion for "scrollback works like Claude Code".
    assert "hello world" in screen_contents, (
        f"user turn body missing from screen: {screen_contents!r}"
    )
    assert "the assistant response" in screen_contents, (
        f"assistant body missing from screen: {screen_contents!r}"
    )


def test_no_alt_screen_escape_emitted() -> None:
    buffer = io.BytesIO()
    text_writer = io.TextIOWrapper(buffer, encoding="utf-8", write_through=True)
    console = Console(
        file=text_writer,
        force_terminal=True,
        force_interactive=False,
        width=120,
        color_system=None,
    )
    transcript = TerminalTranscript(console)
    transcript.push_message(ChatMessage(kind=MessageKind.USER, sender="you", body="x"))
    transcript.push_message(
        ChatMessage(kind=MessageKind.AGENT, sender="agent", body="y")
    )
    text_writer.flush()
    raw = buffer.getvalue()
    # Alt-screen on
    assert b"\x1b[?1049h" not in raw
    # Alt-screen off (would also be present if on was)
    assert b"\x1b[?1049l" not in raw


def test_multiple_turns_all_persist_in_screen() -> None:

    def _run(console):
        transcript = TerminalTranscript(console)
        for i in range(3):
            transcript.push_message(
                ChatMessage(kind=MessageKind.USER, sender="you", body=f"question {i}")
            )
            asyncio.run(
                _run_agent_turn(
                    text=f"question {i}",
                    runtime=_StubRuntime(f"answer {i}"),
                    transcript=transcript,
                    status_line=None,
                )
            )

    screen_contents = _capture_terminal_output(_run)
    for i in range(3):
        assert f"question {i}" in screen_contents
        assert f"answer {i}" in screen_contents
