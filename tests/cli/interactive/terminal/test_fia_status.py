from __future__ import annotations

import asyncio
import io
from typing import Any

from rich.console import Console

from openminion.cli.interactive.terminal.shell import (
    _SLASH_COMMANDS,
    _handle_slash,
    _render_status_block,
)
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine
from openminion.cli.interactive.terminal.transcript import TerminalTranscript


class _FakeRuntime:
    def __init__(
        self,
        *,
        agent_id: str = "openminion",
        provider_name: str = "openai",
        model_name: str = "gpt-4",
        service_vendor_name: str = "openai",
        transport_adapter_name: str = "",
        session_id: str = "test-session-123",
        usage: Any = None,
    ) -> None:
        self.agent_id = agent_id
        self.provider_name = provider_name
        self.model_name = model_name
        self.service_vendor_name = service_vendor_name
        self.transport_adapter_name = transport_adapter_name
        self.session_id = session_id
        self._usage = usage

    def token_usage_snapshot(self) -> Any:
        return self._usage

    def is_room_session(self) -> bool:
        return False


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
    runtime.permission_mode = "readonly"
    runtime.added_workspace_root_count = 2
    console, buf = _make_console()
    _render_status_block(runtime=runtime, console=console, working_dir="/work/dir")
    out = buf.getvalue()
    assert "openminion" in out
    assert "model: gpt-4" in out
    assert "provider: openai" in out
    assert "openai/gpt-4" not in out
    assert "test-session-123" in out
    assert "/work/dir" in out
    assert "permissions: readonly" in out
    assert "added directories: 2" in out


def test_render_status_block_no_usage_shows_hint() -> None:
    runtime = _FakeRuntime(usage=None)
    console, buf = _make_console()
    _render_status_block(runtime=runtime, console=console, working_dir="/tmp")
    out = buf.getvalue()
    # No real usage data → defensive hint.
    assert "no usage data" in out or "usage:" in out


def test_render_status_block_shows_room_facts() -> None:
    runtime = _FakeRuntime(session_id="room-review")
    runtime.is_room_session = lambda: True
    runtime.room_participants_report = lambda: (
        "Room: Review room\n"
        "  key: room:review\n"
        "  routing: sequential\n"
        "  local human: owner-local (owner)\n"
        "  active agent: alpha\n"
        "  participants: 3"
    )
    console, buf = _make_console()

    _render_status_block(runtime=runtime, console=console, working_dir="/work")

    out = buf.getvalue()
    for fact in (
        "Review room",
        "room:review",
        "sequential",
        "owner-local (owner)",
        "active agent: alpha",
        "participants: 3",
    ):
        assert fact in out


def test_render_status_block_separates_nvidia_service_from_openai_api() -> None:
    runtime = _FakeRuntime(
        model_name="google/gemma-4-31b-it",
        service_vendor_name="nvidia",
        transport_adapter_name="openai_chat",
    )
    console, buf = _make_console()
    _render_status_block(runtime=runtime, console=console, working_dir="/work/dir")
    out = buf.getvalue()

    assert "model: google/gemma-4-31b-it" in out
    assert "provider: nvidia" in out
    assert "API adapter: OpenAI-compatible" in out
    assert "openai/google/gemma-4-31b-it" not in out


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
    assert "model: gpt-4" in out
    assert "provider: openai" in out


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
