from __future__ import annotations

import shutil
import subprocess

import pytest

from openminion.cli.tui.presentation.git.diff import (
    build_git_diff_command,
    render_git_diff,
)


def _git(*args: str, cwd) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_build_git_diff_command_bare() -> None:
    assert build_git_diff_command("") == ["git", "diff"]


def test_build_git_diff_command_staged_file() -> None:
    assert build_git_diff_command("--staged README.md") == [
        "git",
        "diff",
        "--staged",
        "--",
        "README.md",
    ]


def test_build_git_diff_command_rejects_extra_args() -> None:
    with pytest.raises(ValueError, match="usage"):
        build_git_diff_command("one two")


def test_render_git_diff_non_git_repo_is_graceful(tmp_path) -> None:
    result = render_git_diff(tmp_path)
    assert result.has_diff is False
    assert "no pending changes" in result.display_body


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_render_git_diff_returns_unified_diff_for_tracked_file(tmp_path) -> None:
    _git("init", cwd=tmp_path)
    path = tmp_path / "note.txt"
    path.write_text("old\n", encoding="utf-8")
    _git("add", "note.txt", cwd=tmp_path)

    path.write_text("new\n", encoding="utf-8")
    result = render_git_diff(tmp_path, "note.txt")

    assert result.has_diff is True
    assert "diff --git" in result.output
    assert "-old" in result.output
    assert "+new" in result.output


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_render_git_diff_supports_staged_diff(tmp_path) -> None:
    _git("init", cwd=tmp_path)
    path = tmp_path / "note.txt"
    path.write_text("staged\n", encoding="utf-8")
    _git("add", "note.txt", cwd=tmp_path)

    result = render_git_diff(tmp_path, "--staged note.txt")

    assert result.has_diff is True
    assert "diff --git" in result.output
    assert "+staged" in result.output
