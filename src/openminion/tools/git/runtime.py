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
    GIT_AUTH_FAILED,
    GIT_BINARY_ERROR,
    GIT_DIRTY_WORKING_TREE,
    GIT_MERGE_CONFLICT,
    GIT_NON_FAST_FORWARD,
    GIT_NOT_A_REPOSITORY,
    GIT_NOT_AVAILABLE,
    GIT_REF_NOT_FOUND,
    GIT_REMOTE_NOT_FOUND,
)

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
        if not current.is_relative_to(seed):
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
            if not current.is_relative_to(seed):
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


def run_git(
    args: tuple[str, ...] | list[str],
    *,
    cwd: str | Path,
    timeout: float = DEFAULT_GIT_TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
) -> GitCommandResult:
    """Run Git with explicit arguments and return its captured result."""

    binary = shutil.which("git")
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
        exit_code=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        cwd=str(cwd),
    )


def classify_git_failure(result: GitCommandResult) -> ToolRuntimeError:
    if result.exit_code == 0:
        raise ValueError(
            "classify_git_failure called with successful result; "
            "check exit_code before classifying"
        )

    stderr_lower = result.stderr.lower()
    output_lower = f"{result.stdout}\n{result.stderr}".lower()
    stderr_excerpt = result.stderr[:MAX_STDERR_DETAIL_CHARS]

    remote_failure = _classify_remote_failure(
        result,
        output_lower=output_lower,
        stderr_lower=stderr_lower,
        stderr_excerpt=stderr_excerpt,
    )
    if remote_failure is not None:
        return remote_failure

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
        or "needed a single revision" in stderr_lower
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


def _classify_remote_failure(
    result: GitCommandResult,
    *,
    output_lower: str,
    stderr_lower: str,
    stderr_excerpt: str,
) -> ToolRuntimeError | None:
    details = {
        "cwd": result.cwd,
        "exit_code": result.exit_code,
        "stderr": stderr_excerpt,
    }
    if "non-fast-forward" in output_lower or "fetch first" in output_lower:
        return ToolRuntimeError(
            GIT_NON_FAST_FORWARD,
            "remote ref update is not a fast-forward",
            details,
        )
    if any(
        token in stderr_lower
        for token in (
            "authentication failed",
            "could not read username",
            "could not read password",
            "permission denied (publickey)",
            "terminal prompts disabled",
        )
    ):
        return ToolRuntimeError(
            GIT_AUTH_FAILED,
            "git remote authentication failed",
            details,
        )
    if "does not appear to be a git repository" in stderr_lower:
        return ToolRuntimeError(
            GIT_REMOTE_NOT_FOUND,
            "configured git remote was not found",
            details,
        )
    return None


def require_configured_git_remote(cwd: str | Path, remote: str) -> None:
    result = run_git(("remote", "get-url", remote), cwd=cwd)
    if result.exit_code != 0:
        raise ToolRuntimeError(
            GIT_REMOTE_NOT_FOUND,
            f"configured git remote not found: {remote}",
            {"cwd": str(cwd), "remote": remote},
        )


def resolve_git_ref_oid(cwd: str | Path, ref: str) -> str:
    result = run_git(
        ("rev-parse", "--verify", "--end-of-options", ref),
        cwd=cwd,
    )
    if result.exit_code != 0:
        raise classify_git_failure(result)
    return result.stdout.strip()


def resolve_git_remote_ref_oid(
    cwd: str | Path,
    *,
    remote: str,
    ref: str,
) -> str | None:
    result = run_git(("ls-remote", remote, ref), cwd=cwd)
    if result.exit_code != 0:
        raise classify_git_failure(result)
    for line in result.stdout.splitlines():
        oid, separator, observed_ref = line.partition("\t")
        if separator and observed_ref == ref:
            return oid
    return None
