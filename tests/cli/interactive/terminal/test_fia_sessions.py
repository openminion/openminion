from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from typing import Any

from rich.console import Console

from openminion.cli.interactive.terminal.shell import (
    _SLASH_COMMANDS,
    _handle_slash,
    _render_sessions_list,
)
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine
from openminion.cli.interactive.terminal.transcript import TerminalTranscript


@dataclass
class _FakeItem:
    id: str
    label: str
    active: bool = False
    meta: dict | None = None


class _FakeRuntime:
    def __init__(self, items: list[_FakeItem] | None = None) -> None:
        self._items = items or []

    def list_sessions(self, *, scope: str = "all") -> list[_FakeItem]:
        return list(self._items)


class _StubOverlay:
    pass


def _make_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    return console, buf


async def _dispatch(text: str, *, runtime: Any) -> tuple[str, bool]:
    console, buf = _make_console()
    transcript = TerminalTranscript(console)
    result = await _handle_slash(
        text,
        runtime=runtime,
        console=console,
        transcript=transcript,
        overlay=_StubOverlay(),  # type: ignore[arg-type]
        status_line=TerminalStatusLine(),
        working_dir="/tmp",
    )
    return buf.getvalue(), result


# ── /sessions catalog ────────────────────────────────────────────


def test_sessions_in_catalog() -> None:
    assert "/sessions" in _SLASH_COMMANDS


# ── Render helper ────────────────────────────────────────────────


def test_render_sessions_list_with_entries() -> None:
    items = [
        _FakeItem(
            id="abc-123-456789",
            label="abc-123",
            active=False,
            meta={
                "channel": "focus",
                "target": "focus",
                "updated_at": "2026-05-13T10:00:00",
            },
        ),
        _FakeItem(
            id="def-789-012345",
            label="def-789",
            active=True,
            meta={
                "channel": "focus",
                "target": "focus",
                "updated_at": "2026-05-13T11:00:00",
            },
        ),
    ]
    runtime = _FakeRuntime(items=items)
    console, buf = _make_console()
    _render_sessions_list(runtime=runtime, console=console)
    out = buf.getvalue()
    assert "abc-123-456789" in out
    assert "def-789-012345" in out
    # Active session marker present.
    assert "◆" in out


def test_render_sessions_list_empty() -> None:
    runtime = _FakeRuntime(items=[])
    console, buf = _make_console()
    _render_sessions_list(runtime=runtime, console=console)
    assert "(no sessions)" in buf.getvalue()


def test_render_sessions_list_missing_lister() -> None:

    class _Bare:
        pass

    console, buf = _make_console()
    _render_sessions_list(runtime=_Bare(), console=console)
    out = buf.getvalue()
    assert "does not expose list_sessions" in out


def test_render_sessions_list_handles_raise() -> None:

    class _Raises:
        def list_sessions(self, *, scope: str = "all") -> Any:
            raise RuntimeError("boom")

    console, buf = _make_console()
    _render_sessions_list(runtime=_Raises(), console=console)
    out = buf.getvalue()
    assert "error" in out
    assert "boom" in out


# ── End-to-end via _handle_slash ─────────────────────────────────


def test_slash_sessions_dispatch_renders_table() -> None:
    items = [_FakeItem(id="aaa", label="aaa", active=True, meta={})]
    runtime = _FakeRuntime(items=items)
    out, exit_code = asyncio.run(_dispatch("/sessions", runtime=runtime))
    assert exit_code is False  # shell stays open
    assert "aaa" in out


def test_slash_sessions_dispatch_empty() -> None:
    runtime = _FakeRuntime(items=[])
    out, _ = asyncio.run(_dispatch("/sessions", runtime=runtime))
    assert "(no sessions)" in out


def test_slash_sessions_does_NOT_fall_through_to_unimplemented() -> None:
    runtime = _FakeRuntime(items=[])
    out, _ = asyncio.run(_dispatch("/sessions", runtime=runtime))
    assert "not yet implemented" not in out
