import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.file.plugin import (
    _resolve_relative_base_dir,
    _resolve_workspace_root,
)

from openminion.tools.git.errors import (
    GIT_AMBIGUOUS_WORKSPACE,
    GIT_BINARY_ERROR,
    GIT_DIRTY_WORKING_TREE,
    GIT_MERGE_CONFLICT,
    GIT_NOT_A_REPOSITORY,
    GIT_NOT_AVAILABLE,
    GIT_REF_NOT_FOUND,
)

# Bounded timeout for any single `git` invocation. Read-only ops (status,
DEFAULT_GIT_TIMEOUT_SECONDS = 30.0

# Maximum stderr length carried in error details. Prevents unbounded growth
# in tool result envelopes when git prints verbose diagnostics.
MAX_STDERR_DETAIL_CHARS = 1000


def _has_git_entry(path: Path) -> bool:
    return path.joinpath(".git").exists()


def _candidate_child_repos(seed: Path) -> list[Path]:
    if not seed.exists() or not seed.is_dir():
        return []
    return sorted(
        child.resolve(strict=False)
        for child in seed.iterdir()
        if child.is_dir() and _has_git_entry(child)
    )


def _search_path_chain(seed: Path, preferred: Path) -> list[str]:
    chain = [str(seed)]
    if preferred == seed:
        return chain

    current = preferred.resolve(strict=False)
    while True:
        chain.append(str(current))
        if current == seed:
            break
        try:
            current.relative_to(seed)
        except ValueError:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return chain


def resolve_git_repo_root(ctx: Any) -> Path:
    """Resolve a deterministic git repo root from the workspace seed."""

    seed = _resolve_workspace_root(ctx).resolve(strict=False)
    if _has_git_entry(seed):
        return seed

    preferred = _resolve_relative_base_dir(ctx).resolve(strict=False)
    searched_paths = _search_path_chain(seed, preferred)

    if preferred != seed:
        current = preferred
        while True:
            try:
                current.relative_to(seed)
            except ValueError:
                break
            if _has_git_entry(current):
                return current
            if current == seed:
                break
            parent = current.parent
            if parent == current:
                break
            current = parent

    candidates = _candidate_child_repos(seed)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ToolRuntimeError(
            GIT_AMBIGUOUS_WORKSPACE,
            "workspace root maps to multiple git repositories",
            {
                "candidate": str(seed),
                "searched_paths": searched_paths,
                "candidates": [str(item) for item in candidates],
                "preferred_path": str(preferred),
            },
        )

    raise ToolRuntimeError(
        GIT_NOT_A_REPOSITORY,
        f"workspace is not a git repository: {seed}",
        {
            "candidate": str(seed),
            "searched_paths": searched_paths,
        },
    )


@dataclass(frozen=True)
class GitCommandResult:
    """Outcome of a single `git` invocation."""

    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    cwd: str


def _git_binary_path() -> str | None:
    """Return the absolute path to `git` on `PATH`, or `None` if not found."""

    return shutil.which("git")


def run_git(
    args: tuple[str, ...] | list[str],
    *,
    cwd: str | Path,
    timeout: float = DEFAULT_GIT_TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
) -> GitCommandResult:
    """Run `git <args>` in `cwd`. Raises `ToolRuntimeError(GIT_NOT_AVAILABLE)`
    if the binary is missing. Otherwise returns the captured result; the
    caller classifies exit codes via `classify_git_failure`.
    """

    binary = _git_binary_path()
    if binary is None:
        raise ToolRuntimeError(
            GIT_NOT_AVAILABLE,
            "git binary not found on PATH",
            {"PATH_lookup": "git"},
        )

    command = (binary, *tuple(args))
    completed = subprocess.run(  # noqa: S603 - explicit argv, no shell
        list(command),
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    return GitCommandResult(
        command=command,
        exit_code=int(completed.returncode),
        stdout=str(completed.stdout or ""),
        stderr=str(completed.stderr or ""),
        cwd=str(cwd),
    )


def classify_git_failure(result: GitCommandResult) -> ToolRuntimeError:
    """Map a non-zero `GitCommandResult` to a deterministic"""

    if result.exit_code == 0:
        # Defensive: callers should only invoke this on failure. Returning
        raise ValueError(
            "classify_git_failure called with successful result; "
            "check exit_code before classifying"
        )

    stderr_lower = result.stderr.lower()
    stderr_excerpt = result.stderr[:MAX_STDERR_DETAIL_CHARS]

    if "not a git repository" in stderr_lower:
        return ToolRuntimeError(
            GIT_NOT_A_REPOSITORY,
            f"workspace is not a git repository: {result.cwd}",
            {
                "cwd": result.cwd,
                "exit_code": result.exit_code,
                "stderr": stderr_excerpt,
            },
        )

    if (
        "unknown revision" in stderr_lower
        or "bad revision" in stderr_lower
        or "ambiguous argument" in stderr_lower
        or "did not match any file(s) known to git" in stderr_lower
    ):
        return ToolRuntimeError(
            GIT_REF_NOT_FOUND,
            "git ref not found",
            {
                "cwd": result.cwd,
                "exit_code": result.exit_code,
                "stderr": stderr_excerpt,
                "command": list(result.command[1:]),
            },
        )

    if (
        "your local changes" in stderr_lower
        or "would be overwritten" in stderr_lower
        or "uncommitted changes" in stderr_lower
    ):
        return ToolRuntimeError(
            GIT_DIRTY_WORKING_TREE,
            "operation requires a clean working tree",
            {
                "cwd": result.cwd,
                "exit_code": result.exit_code,
                "stderr": stderr_excerpt,
            },
        )

    if "conflict" in stderr_lower or "needs merge" in stderr_lower:
        return ToolRuntimeError(
            GIT_MERGE_CONFLICT,
            "git merge conflict",
            {
                "cwd": result.cwd,
                "exit_code": result.exit_code,
                "stderr": stderr_excerpt,
            },
        )

    return ToolRuntimeError(
        GIT_BINARY_ERROR,
        f"git command failed (exit {result.exit_code})",
        {
            "cwd": result.cwd,
            "exit_code": result.exit_code,
            "stderr": stderr_excerpt,
            "command": list(result.command[1:]),
        },
    )
