from __future__ import annotations

import asyncio
import io
from typing import Any

from rich.console import Console

from openminion.cli.tui.terminal.shell import (
    _SLASH_COMMANDS,
    _handle_slash,
    _render_tools_list,
)
from openminion.cli.tui.terminal.status_line import TerminalStatusLine
from openminion.cli.tui.terminal.transcript import TerminalTranscript


class _FakeRuntime:
    def __init__(self, pairs: list[tuple[str, bool]] | None = None) -> None:
        self._pairs = pairs or []

    def list_tools(self) -> list[tuple[str, bool]]:
        return list(self._pairs)


class _StubOverlay:
    pass


def _make_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    return console, buf


async def _dispatch(runtime: Any) -> str:
    console, buf = _make_console()
    await _handle_slash(
        "/tools",
        runtime=runtime,
        console=console,
        transcript=TerminalTranscript(console),
        overlay=_StubOverlay(),  # type: ignore[arg-type]
        status_line=TerminalStatusLine(),
        working_dir="/tmp",
    )
    return buf.getvalue()


def test_tools_in_catalog() -> None:
    assert "/tools" in _SLASH_COMMANDS


def test_render_tools_list_with_entries() -> None:
    runtime = _FakeRuntime(
        pairs=[("Bash", True), ("Read", True), ("DangerTool", False)]
    )
    console, buf = _make_console()
    _render_tools_list(runtime=runtime, console=console)
    out = buf.getvalue()
    assert "Bash" in out
    assert "Read" in out
    assert "DangerTool" in out
    assert "enabled" in out
    assert "disabled" in out


def test_render_tools_list_empty() -> None:
    runtime = _FakeRuntime(pairs=[])
    console, buf = _make_console()
    _render_tools_list(runtime=runtime, console=console)
    assert "(no tools registered)" in buf.getvalue()


def test_render_tools_list_missing_lister() -> None:
    class _Bare:
        pass

    console, buf = _make_console()
    _render_tools_list(runtime=_Bare(), console=console)
    assert "does not expose list_tools" in buf.getvalue()


def test_render_tools_list_handles_raise() -> None:
    class _Raises:
        def list_tools(self) -> Any:
            raise RuntimeError("kaboom")

    console, buf = _make_console()
    _render_tools_list(runtime=_Raises(), console=console)
    out = buf.getvalue()
    assert "error" in out
    assert "kaboom" in out


def test_slash_tools_dispatch_renders_table() -> None:
    runtime = _FakeRuntime(pairs=[("Edit", True)])
    out = asyncio.run(_dispatch(runtime))
    assert "Edit" in out


def test_slash_tools_does_NOT_fall_through() -> None:
    runtime = _FakeRuntime(pairs=[])
    out = asyncio.run(_dispatch(runtime))
    assert "not yet implemented" not in out
