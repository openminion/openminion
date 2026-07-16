from __future__ import annotations

import asyncio
import io

from rich.console import Console

from openminion.cli.interactive.terminal.shell import (
    _SLASH_COMMANDS,
    _handle_slash,
)
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine
from openminion.cli.interactive.terminal.transcript import TerminalTranscript
from openminion.cli.presentation.models import (
    ChatMessage,
    MessageKind,
    ToolEvent,
)


def _make_console_and_transcript(
    verbosity: str = "normal",
) -> tuple[Console, TerminalTranscript, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    return console, TerminalTranscript(console, verbosity=verbosity), buf


class _StubOverlay:
    pass


async def _dispatch(
    text: str,
    *,
    transcript: TerminalTranscript,
    console: Console,
    runtime=None,
) -> bool:
    return await _handle_slash(
        text,
        runtime=runtime,
        console=console,
        transcript=transcript,
        overlay=_StubOverlay(),  # type: ignore[arg-type]
        status_line=TerminalStatusLine(),
        working_dir="/tmp",
    )


def _push_long_tool(transcript: TerminalTranscript, *, lines: int = 20) -> None:
    body = "\n".join(f"line {i}" for i in range(1, lines + 1))
    event = ToolEvent(
        tool_name="Bash",
        args={"cmd": "echo"},
        content=body,
        full_content=body,
        exit_code=0,
    )
    transcript.push_message(
        ChatMessage(
            kind=MessageKind.TOOL,
            sender="tool:Bash",
            body="",
            tool_event=event,
        )
    )


def test_quiet_in_slash_catalog() -> None:
    assert "/quiet" in _SLASH_COMMANDS


def test_verbose_in_slash_catalog() -> None:
    assert "/verbose" in _SLASH_COMMANDS


def test_normal_in_slash_catalog() -> None:
    assert "/normal" in _SLASH_COMMANDS


def test_details_in_slash_catalog() -> None:
    assert "/details" in _SLASH_COMMANDS


def test_slash_quiet_flips_verbosity() -> None:
    console, t, buf = _make_console_and_transcript("normal")
    asyncio.run(_dispatch("/quiet", transcript=t, console=console))
    assert t._verbosity == "quiet"
    output = buf.getvalue()
    assert "verbosity: quiet" in output


def test_slash_quiet_hint_text() -> None:
    console, t, buf = _make_console_and_transcript("normal")
    asyncio.run(_dispatch("/quiet", transcript=t, console=console))
    assert "/normal" in buf.getvalue() or "/verbose" in buf.getvalue()


def test_slash_verbose_flips_verbosity() -> None:
    console, t, buf = _make_console_and_transcript("quiet")
    asyncio.run(_dispatch("/verbose", transcript=t, console=console))
    assert t._verbosity == "verbose"
    assert "verbosity: verbose" in buf.getvalue()


def test_slash_verbose_after_quiet_renders_full() -> None:
    console, t, buf = _make_console_and_transcript("quiet")
    _push_long_tool(t, lines=10)
    pre_len = len(buf.getvalue())
    asyncio.run(_dispatch("/verbose", transcript=t, console=console))
    _push_long_tool(t, lines=10)
    post = buf.getvalue()[pre_len:]
    assert "line 1" in post
    assert "line 10" in post


def test_slash_normal_flips_verbosity() -> None:
    console, t, buf = _make_console_and_transcript("verbose")
    asyncio.run(_dispatch("/normal", transcript=t, console=console))
    assert t._verbosity == "normal"
    assert "verbosity: normal" in buf.getvalue()


def test_slash_normal_after_verbose_truncates() -> None:
    console, t, buf = _make_console_and_transcript("verbose")
    asyncio.run(_dispatch("/normal", transcript=t, console=console))
    pre_len = len(buf.getvalue())
    _push_long_tool(t, lines=20)
    post = buf.getvalue()[pre_len:]
    assert "… +" in post
    assert "/expand" in post


def test_slash_override_persists_across_multiple_tool_blocks() -> None:
    console, t, _ = _make_console_and_transcript("normal")
    asyncio.run(_dispatch("/quiet", transcript=t, console=console))
    _push_long_tool(t, lines=20)
    _push_long_tool(t, lines=20)
    _push_long_tool(t, lines=20)
    assert t._hidden_tool_count == 3


def test_slash_override_beats_launch_resolution() -> None:
    console, t, _ = _make_console_and_transcript("verbose")
    assert t._verbosity == "verbose"
    asyncio.run(_dispatch("/quiet", transcript=t, console=console))
    assert t._verbosity == "quiet"


def test_slash_details_toggles_verbose_and_normal() -> None:
    console, t, buf = _make_console_and_transcript("normal")
    asyncio.run(_dispatch("/details", transcript=t, console=console))
    assert t._verbosity == "verbose"
    assert "details on" in buf.getvalue()

    asyncio.run(_dispatch("/details", transcript=t, console=console))
    assert t._verbosity == "normal"
    assert "details off" in buf.getvalue()


def test_slash_details_accepts_quiet_arg() -> None:
    console, t, buf = _make_console_and_transcript("normal")
    asyncio.run(_dispatch("/details quiet", transcript=t, console=console))
    assert t._verbosity == "quiet"
    assert "details quiet" in buf.getvalue()


def test_export_and_editor_guidance_are_terminal_commands() -> None:
    class _Runtime:
        session_id = "session-123"

    console, t, buf = _make_console_and_transcript("normal")
    asyncio.run(_dispatch("/export", transcript=t, console=console, runtime=_Runtime()))
    asyncio.run(_dispatch("/editor", transcript=t, console=console, runtime=_Runtime()))
    output = buf.getvalue()
    assert "openminion export transcript --session-id session-123" in output
    assert "External-editor" in output or "external-editor" in output


def test_set_verbosity_helper_validates() -> None:
    _, t, _ = _make_console_and_transcript("normal")
    t.set_verbosity("quiet")
    assert t._verbosity == "quiet"
    t.set_verbosity("verbose")
    assert t._verbosity == "verbose"
    t.set_verbosity("normal")
    assert t._verbosity == "normal"


def test_set_verbosity_helper_coerces_garbage_to_normal() -> None:
    _, t, _ = _make_console_and_transcript("verbose")
    t.set_verbosity("loud")
    assert t._verbosity == "normal"


def test_slash_catalog_no_duplicates() -> None:
    assert len(_SLASH_COMMANDS) == len(set(_SLASH_COMMANDS))
