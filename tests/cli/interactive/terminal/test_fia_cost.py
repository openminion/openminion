from __future__ import annotations

import asyncio
import io
from typing import Any

from rich.console import Console

from openminion.cli.interactive.terminal.shell import (
    _SLASH_COMMANDS,
    _handle_slash,
    _render_cost_snapshot,
)
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine
from openminion.cli.interactive.terminal.transcript import TerminalTranscript


class _FakeRuntime:
    def __init__(self, snapshot: Any = None, raises: bool = False) -> None:
        self._snapshot = snapshot
        self._raises = raises

    def token_usage_snapshot(self) -> Any:
        if self._raises:
            raise RuntimeError("snapshot failed")
        return self._snapshot

    def token_usage_report(self, *, recent: int | None = None) -> str:
        if recent is not None:
            return f"Token history · requested={recent}"
        return "Token usage\nTokens: 42 total"


class _StubOverlay:
    pass


def _make_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    return console, buf


async def _dispatch(runtime: Any, command: str = "/cost") -> str:
    console, buf = _make_console()
    await _handle_slash(
        command,
        runtime=runtime,
        console=console,
        transcript=TerminalTranscript(console),
        overlay=_StubOverlay(),  # type: ignore[arg-type]
        status_line=TerminalStatusLine(),
        working_dir="/tmp",
    )
    return buf.getvalue()


def test_cost_in_catalog() -> None:
    assert "/cost" in _SLASH_COMMANDS
    assert "/tokens" in _SLASH_COMMANDS


def test_render_cost_snapshot_missing_method() -> None:
    console, buf = _make_console()
    _render_cost_snapshot(runtime=object(), console=console)
    assert "does not expose token_usage_snapshot" in buf.getvalue()


def test_render_cost_snapshot_handles_raise() -> None:
    console, buf = _make_console()
    _render_cost_snapshot(runtime=_FakeRuntime(raises=True), console=console)
    out = buf.getvalue()
    assert "error" in out


def test_render_cost_snapshot_no_data_hint() -> None:
    # An empty snapshot will likely have format_token_usage_summary
    # return "" — but if not, the test still passes (either path
    # is acceptable: dim hint OR a "cost:" line).
    runtime = _FakeRuntime(snapshot=None)
    console, buf = _make_console()
    _render_cost_snapshot(runtime=runtime, console=console)
    out = buf.getvalue()
    # Either branch is acceptable; what matters is no crash.
    assert "cost" in out.lower() or "no usage data" in out


def test_slash_cost_does_NOT_fall_through() -> None:
    runtime = _FakeRuntime(snapshot=None)
    out = asyncio.run(_dispatch(runtime))
    assert "not yet implemented" not in out


def test_slash_cost_dispatch_runs() -> None:
    runtime = _FakeRuntime(snapshot=None)
    out = asyncio.run(_dispatch(runtime))
    # Some content rendered (either hint or summary).
    assert len(out.strip()) > 0


def test_slash_tokens_dispatches_durable_report() -> None:
    runtime = _FakeRuntime(snapshot=None)
    out = asyncio.run(_dispatch(runtime, "/tokens"))

    assert "Token usage" in out
    assert "42 total" in out


def test_slash_tokens_supports_recent_history() -> None:
    runtime = _FakeRuntime(snapshot=None)

    default = asyncio.run(_dispatch(runtime, "/tokens recent"))
    explicit = asyncio.run(_dispatch(runtime, "/tokens recent 5"))
    invalid = asyncio.run(_dispatch(runtime, "/tokens recent 21"))

    assert "requested=10" in default
    assert "requested=5" in explicit
    assert invalid.strip() == "usage: /tokens [recent [1..20]]"
