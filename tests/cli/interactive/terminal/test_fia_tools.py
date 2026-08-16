from __future__ import annotations

import asyncio
import io
from typing import Any

from rich.console import Console

from openminion.cli.interactive.terminal.shell import (
    _SLASH_COMMANDS,
    _handle_slash,
    _render_tools_list,
)
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine
from openminion.cli.interactive.terminal.transcript import TerminalTranscript


class _FakeRuntime:
    def __init__(self, pairs: list[tuple[str, bool]] | None = None) -> None:
        self._pairs = pairs or []
        self.active = False

    def list_tools(self) -> list[tuple[str, bool]]:
        return list(self._pairs)

    def tool_exposure_status(self) -> dict[str, Any]:
        return {
            "profiles": [
                {
                    "profile_id": "security_readonly",
                    "tier": "read",
                    "active": self.active,
                }
            ]
        }

    def activate_tool_profile(self, profile_id: str, **kwargs: Any) -> dict[str, str]:
        assert kwargs["approved"] is True
        self.active = True
        return {"profile_id": profile_id, "audit_id": "audit-1"}

    def deactivate_tool_profile(self, profile_id: str, **kwargs: Any) -> bool:
        self.active = False
        return True


class _StubOverlay:
    pass


def _make_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    return console, buf


async def _dispatch(runtime: Any, text: str = "/tools") -> str:
    console, buf = _make_console()
    await _handle_slash(
        text,
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
    console, buf = _make_console()
    _render_tools_list(runtime=object(), console=console)
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


def test_slash_tools_dispatches_exposure_commands() -> None:
    runtime = _FakeRuntime()
    status = asyncio.run(_dispatch(runtime, "/tools status"))
    activated = asyncio.run(
        _dispatch(runtime, "/tools activate security_readonly approved=yes")
    )

    assert "hidden  security_readonly  (read)" in status
    assert "Activated: security_readonly (audit-1)" in activated


def test_slash_tools_does_NOT_fall_through() -> None:
    runtime = _FakeRuntime(pairs=[])
    out = asyncio.run(_dispatch(runtime))
    assert "not yet implemented" not in out
