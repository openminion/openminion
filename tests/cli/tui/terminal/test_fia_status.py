from __future__ import annotations

import asyncio
import io
from typing import Any

from rich.console import Console

from openminion.cli.tui.terminal.shell import (
    _SLASH_COMMANDS,
    _handle_slash,
    _render_status_block,
)
from openminion.cli.tui.terminal.status_line import TerminalStatusLine
from openminion.cli.tui.terminal.transcript import TerminalTranscript


class _FakeRuntime:
    def __init__(
        self,
        *,
        agent_id: str = "openminion",
        provider_name: str = "openai",
        model_name: str = "gpt-4",
        session_id: str = "test-session-123",
        usage: Any = None,
    ) -> None:
        self.agent_id = agent_id
        self.provider_name = provider_name
        self.model_name = model_name
        self.session_id = session_id
        self._usage = usage

    def token_usage_snapshot(self) -> Any:
        return self._usage


class _StubOverlay:
    pass


def _make_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    return console, buf


async def _dispatch(text: str, *, runtime: Any, working_dir: str = "/tmp/test") -> str:
    console, buf = _make_console()
    transcript = TerminalTranscript(console)
    await _handle_slash(
        text,
        runtime=runtime,
        console=console,
        transcript=transcript,
        overlay=_StubOverlay(),  # type: ignore[arg-type]
        status_line=TerminalStatusLine(),
        working_dir=working_dir,
    )
    return buf.getvalue()


def test_status_in_catalog() -> None:
    assert "/status" in _SLASH_COMMANDS


def test_render_status_block_shows_agent_model_cwd() -> None:
    runtime = _FakeRuntime()
    console, buf = _make_console()
    _render_status_block(runtime=runtime, console=console, working_dir="/work/dir")
    out = buf.getvalue()
    assert "openminion" in out
    assert "openai/gpt-4" in out
    assert "test-session-123" in out
    assert "/work/dir" in out


def test_render_status_block_no_usage_shows_hint() -> None:
    runtime = _FakeRuntime(usage=None)
    console, buf = _make_console()
    _render_status_block(runtime=runtime, console=console, working_dir="/tmp")
    out = buf.getvalue()
    # No real usage data → defensive hint.
    assert "no usage data" in out or "usage:" in out


def test_render_status_block_handles_usage_format_error() -> None:
    class _BadUsageRuntime(_FakeRuntime):
        def token_usage_snapshot(self) -> Any:
            raise ValueError("bad usage state")

    runtime = _BadUsageRuntime()
    console, buf = _make_console()
    _render_status_block(runtime=runtime, console=console, working_dir="/tmp")

    out = buf.getvalue()
    assert "Status:" in out
    assert "no usage data" in out or "usage:" in out


def test_slash_status_dispatch_shows_status_block() -> None:
    runtime = _FakeRuntime()
    out = asyncio.run(_dispatch("/status", runtime=runtime, working_dir="/cwd"))
    assert "Status:" in out
    assert "openminion" in out
    assert "openai/gpt-4" in out


def test_slash_status_does_NOT_fall_through() -> None:
    runtime = _FakeRuntime()
    out = asyncio.run(_dispatch("/status", runtime=runtime))
    assert "not yet implemented" not in out


def test_status_handles_missing_attributes_defensively() -> None:

    class _Bare:
        pass

    console, buf = _make_console()
    _render_status_block(runtime=_Bare(), console=console, working_dir="/tmp")
    out = buf.getvalue()
    assert "Status:" in out
    assert "—" in out
