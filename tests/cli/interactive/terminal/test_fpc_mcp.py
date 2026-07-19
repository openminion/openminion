from __future__ import annotations

import asyncio
import io
from typing import Any

from rich.console import Console

from openminion.cli.interactive.terminal.shell import (
    _SLASH_COMMANDS,
    _handle_slash,
    _render_mcp_status,
)
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine
from openminion.cli.interactive.terminal.transcript import TerminalTranscript


class _FakeRuntime:
    def __init__(self, report: str = "MCP servers:\n- fixture  [ready]") -> None:
        self._report = report

    def mcp_status_report(self) -> str:
        return self._report


class _StubOverlay:
    pass


def _make_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    return console, buf


async def _dispatch(runtime: Any) -> str:
    console, buf = _make_console()
    await _handle_slash(
        "/mcp",
        runtime=runtime,
        console=console,
        transcript=TerminalTranscript(console),
        overlay=_StubOverlay(),  # type: ignore[arg-type]
        status_line=TerminalStatusLine(),
        working_dir="/tmp",
    )
    return buf.getvalue()


def test_mcp_in_catalog() -> None:
    assert "/mcp" in _SLASH_COMMANDS


def test_render_mcp_status_with_report() -> None:
    console, buf = _make_console()
    _render_mcp_status(runtime=_FakeRuntime(), console=console)
    out = buf.getvalue()
    assert "MCP servers:" in out
    assert "fixture" in out


def test_render_mcp_status_missing_reporter() -> None:
    class _Bare:
        pass

    console, buf = _make_console()
    _render_mcp_status(runtime=_Bare(), console=console)
    assert "does not expose mcp_status_report" in buf.getvalue()


def test_render_mcp_status_handles_raise() -> None:
    class _Raises:
        def mcp_status_report(self) -> str:
            raise RuntimeError("boom")

    console, buf = _make_console()
    _render_mcp_status(runtime=_Raises(), console=console)
    out = buf.getvalue()
    assert "error" in out
    assert "boom" in out


def test_slash_mcp_dispatch_renders_report() -> None:
    out = asyncio.run(_dispatch(_FakeRuntime()))
    assert "MCP servers:" in out
    assert "fixture" in out
