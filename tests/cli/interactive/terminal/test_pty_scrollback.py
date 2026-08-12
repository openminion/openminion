from __future__ import annotations

import asyncio
import io

import pyte
from prompt_toolkit import PromptSession
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output
from rich.console import Console

from openminion.cli.interactive.terminal.composer import TerminalComposer
from openminion.cli.interactive.terminal.shell import _run_agent_turn
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine
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
        transcript.push_message(
            ChatMessage(kind=MessageKind.USER, sender="you", body="hello world")
        )
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
    assert b"\x1b[?1049h" not in raw
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


def test_busy_status_is_erased_before_queued_input_lands() -> None:
    async def _run() -> str:
        raw = io.StringIO()
        output = Vt100_Output(
            raw,
            get_size=lambda: Size(rows=20, columns=100),
            enable_cpr=False,
        )
        with create_pipe_input() as pipe:
            status_line = TerminalStatusLine()
            status_line.set_state(
                state="responding",
                turn_status="Analyzing request...",
                elapsed_seconds=22,
            )
            composer = TerminalComposer(active_status=status_line.active_status)
            composer._session = PromptSession(
                input=pipe,
                output=output,
                style=composer._session.style,
            )
            composer.set_busy(True)

            async def _submit() -> None:
                await asyncio.sleep(0.05)
                pipe.send_text("queued question\n")

            asyncio.create_task(_submit())
            text = await composer.read_line()
            console = Console(
                file=raw,
                force_terminal=True,
                color_system=None,
                width=100,
            )
            TerminalTranscript(console).render_user_input(text)

        screen = pyte.Screen(100, 20)
        pyte.Stream(screen).feed(raw.getvalue())
        return "\n".join(screen.display)

    screen = asyncio.run(_run())

    assert "queued question" in screen
    assert "Status:" not in screen
    assert "Analyzing request..." not in screen
