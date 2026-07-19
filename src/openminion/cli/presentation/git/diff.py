from __future__ import annotations

from dataclasses import dataclass
import shlex
import shutil
import subprocess
import time
from pathlib import Path

__all__ = ["GitDiffResult", "build_git_diff_command", "render_git_diff"]


_TIMEOUT_SECONDS = 10.0
_NO_CHANGES_MESSAGE = "(no pending changes detected)"


@dataclass(frozen=True)
class GitDiffResult:
    command: tuple[str, ...]
    output: str = ""
    message: str = _NO_CHANGES_MESSAGE
    exit_code: int = 0
    duration_ms: int = 0

    @property
    def has_diff(self) -> bool:
        return bool(self.output.strip())

    @property
    def display_body(self) -> str:
        return self.output.rstrip() if self.has_diff else self.message


def build_git_diff_command(args: str = "") -> list[str]:
    raw = str(args or "").strip()
    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        raise ValueError(f"could not parse /diff args: {exc}") from exc

    if not tokens:
        return ["git", "diff"]

    command = ["git", "diff"]
    if tokens[0] == "--staged":
        command.append("--staged")
        tokens = tokens[1:]

    if not tokens:
        return command
    if len(tokens) == 1:
        return [*command, "--", tokens[0]]
    raise ValueError("usage: /diff [--staged] [path]")


def render_git_diff(working_dir: str | Path, args: str = "") -> GitDiffResult:
    command = build_git_diff_command(args)
    try:
        cwd = Path(str(working_dir or "")).expanduser().resolve(strict=False)
    except (TypeError, ValueError):
        cwd = Path.cwd()
    if not cwd.exists() or not cwd.is_dir():
        return GitDiffResult(
            command=tuple(command),
            message="(diff unavailable: working directory not found)",
            exit_code=1,
        )
    if shutil.which("git") is None:
        return GitDiffResult(
            command=tuple(command),
            message="(diff unavailable: git executable not found)",
            exit_code=1,
        )

    started = time.perf_counter()
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return GitDiffResult(
            command=tuple(command),
            message=f"(diff unavailable: {exc})",
            exit_code=1,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    duration_ms = int((time.perf_counter() - started) * 1000)
    stdout = (result.stdout or "").rstrip()
    stderr = (result.stderr or "").strip()
    if stdout:
        return GitDiffResult(
            command=tuple(command),
            output=stdout,
            exit_code=int(result.returncode or 0),
            duration_ms=duration_ms,
        )
    if (
        result.returncode != 0
        and stderr
        and "not a git repository" not in stderr.lower()
    ):
        return GitDiffResult(
            command=tuple(command),
            message=f"(diff unavailable: {stderr})",
            exit_code=int(result.returncode or 1),
            duration_ms=duration_ms,
        )
    return GitDiffResult(
        command=tuple(command),
        message=_NO_CHANGES_MESSAGE,
        exit_code=0,
        duration_ms=duration_ms,
    )
