from __future__ import annotations

import asyncio
import io
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from openminion.cli.tui.terminal.shell import _SLASH_COMMANDS, _handle_slash


def _git(*args: str, cwd) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _dispatch(text: str, *, working_dir: str, transcript=None) -> tuple[str, MagicMock]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    tx = transcript or MagicMock()
    asyncio.run(
        _handle_slash(
            text,
            runtime=SimpleNamespace(),
            console=console,
            transcript=tx,
            overlay=MagicMock(),
            status_line=MagicMock(),
            working_dir=working_dir,
        )
    )
    return buf.getvalue(), tx


def test_diff_in_slash_catalog() -> None:
    assert "/diff" in _SLASH_COMMANDS


def test_diff_slash_no_diff_prints_muted_message(tmp_path) -> None:
    out, transcript = _dispatch("/diff", working_dir=str(tmp_path))

    assert "no pending changes" in out
    transcript.handle_tool_completed.assert_not_called()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_diff_slash_routes_unified_diff_to_tool_renderer(tmp_path) -> None:
    _git("init", cwd=tmp_path)
    path = tmp_path / "note.txt"
    path.write_text("old\n", encoding="utf-8")
    _git("add", "note.txt", cwd=tmp_path)
    path.write_text("new\n", encoding="utf-8")

    _out, transcript = _dispatch("/diff note.txt", working_dir=str(tmp_path))

    transcript.handle_tool_completed.assert_called_once()
    event = transcript.handle_tool_completed.call_args.args[0]
    assert event.tool_name == "Edit"
    assert "diff --git" in event.content
    assert "-old" in event.content
    assert "+new" in event.content
