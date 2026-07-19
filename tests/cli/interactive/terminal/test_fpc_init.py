from __future__ import annotations

import asyncio
import io
from pathlib import Path

from rich.console import Console

from openminion.cli.interactive.terminal.shell import _SLASH_COMMANDS, _handle_slash
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine
from openminion.cli.interactive.terminal.transcript import TerminalTranscript


class _Runtime:
    agent_id = "alpha"
    provider_name = "openai"
    model_name = "gpt-4"

    def __init__(self) -> None:
        self.context = None

    def set_project_context(self, info) -> None:
        self.context = info


class _Overlay:
    def __init__(self, decision: str) -> None:
        self._decision = decision
        self.prompts: list[str] = []

    def present_approval(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._decision


def _make_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    return console, buf


async def _dispatch(
    text: str, *, runtime: _Runtime, overlay: _Overlay, working_dir: str
):
    console, buf = _make_console()
    transcript = TerminalTranscript(console)
    result = await _handle_slash(
        text,
        runtime=runtime,
        console=console,
        transcript=transcript,
        overlay=overlay,  # type: ignore[arg-type]
        status_line=TerminalStatusLine(),
        working_dir=working_dir,
    )
    return buf.getvalue(), result


def test_init_added_to_catalog() -> None:
    assert "/init" in _SLASH_COMMANDS


def test_init_writes_openminion_md_when_confirmed(tmp_path: Path) -> None:
    runtime = _Runtime()
    overlay = _Overlay("allow")

    out, should_exit = asyncio.run(
        _dispatch("/init", runtime=runtime, overlay=overlay, working_dir=str(tmp_path))
    )

    assert should_exit is False
    target = tmp_path / "OPENMINION.md"
    assert target.is_file()
    assert "wrote" in out.lower()
    assert runtime.context is not None
    assert runtime.context.path == target


def test_init_cancelled_does_not_write(tmp_path: Path) -> None:
    runtime = _Runtime()
    overlay = _Overlay("deny")

    out, _ = asyncio.run(
        _dispatch("/init", runtime=runtime, overlay=overlay, working_dir=str(tmp_path))
    )

    assert "(init cancelled)" in out
    assert not (tmp_path / "OPENMINION.md").exists()


def test_init_refuses_when_legacy_context_exists(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("existing", encoding="utf-8")
    runtime = _Runtime()
    overlay = _Overlay("allow")

    out, _ = asyncio.run(
        _dispatch("/init", runtime=runtime, overlay=overlay, working_dir=str(tmp_path))
    )

    assert "already exists" in out.lower()
    assert not (tmp_path / "OPENMINION.md").exists()
