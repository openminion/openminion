from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.file.plugin import _resolve_path_lexical
from openminion.tools.git.constants import DEFAULT_LOG_LIMIT, MAX_LOG_LIMIT
from openminion.tools.git.errors import (
    GIT_DESTRUCTIVE_NOT_APPROVED,
    GIT_NOTHING_TO_COMMIT,
    GIT_PATH_OUTSIDE_WORKSPACE,
)
from openminion.tools.git.parsers import (
    LOG_PRETTY_FORMAT_ARG,
    blame_to_dict,
    branch_list_to_dict,
    log_to_dict,
    parse_blame_porcelain,
    parse_branch_list,
    parse_log_output,
    parse_reflog_output,
    parse_stash_list,
    parse_status_porcelain_v2,
    reflog_to_dict,
    stash_list_to_dict,
    status_to_dict,
)
from openminion.tools.git.runtime import (
    GitCommandResult,
    classify_git_failure,
    resolve_git_repo_root,
    run_git,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GitStatusArgs(_StrictModel):
    path: str = Field(
        default=".",
        description=(
            "Path within the workspace to scope the status query. "
            "Default is the workspace root."
        ),
    )


class GitDiffArgs(_StrictModel):
    ref_a: str | None = Field(
        default=None,
        description=(
            "First ref. Omit to diff against the index (or against ref_b if "
            "ref_b is provided)."
        ),
    )
    ref_b: str | None = Field(
        default=None,
        description="Second ref. Omit to diff against the working tree.",
    )
    path: str | None = Field(
        default=None,
        description="Optional path to scope the diff to a single file or directory.",
    )
    staged: bool = Field(
        default=False,
        description="If true, diff the index against HEAD (equivalent to --cached).",
    )
    name_only: bool = Field(
        default=False,
        description="If true, list changed paths only; do not include hunks.",
    )


class GitLogArgs(_StrictModel):
    limit: int = Field(
        default=DEFAULT_LOG_LIMIT,
        ge=1,
        le=MAX_LOG_LIMIT,
        description=f"Max number of commits to return (1..{MAX_LOG_LIMIT}).",
    )
    path: str | None = Field(
        default=None,
        description="Optional path to scope the log to a single file or directory.",
    )
    since: str | None = Field(
        default=None,
        description="Optional git --since argument (e.g. '2 weeks ago', '2026-01-01').",
    )
    until: str | None = Field(
        default=None,
        description="Optional git --until argument.",
    )


class GitShowArgs(_StrictModel):
    ref: str = Field(
        ..., min_length=1, description="Commit SHA, branch, tag, or other ref."
    )


class GitBlameArgs(_StrictModel):
    path: str = Field(..., min_length=1, description="Path of file to blame.")
    line: int | None = Field(
        default=None,
        ge=1,
        description="Optional single line number to scope blame to (1-based).",
    )


class GitBranchArgs(_StrictModel):
    action: str = Field(
        ...,
        description=(
            "Branch action discriminator: 'list' (read-only), 'create', or "
            "'delete'. Each action validates its own arg subset; mixing args "
            "across actions is rejected."
        ),
    )
    name: str | None = Field(
        default=None,
        description="Branch name. Required for 'create' and 'delete'.",
    )
    from_ref: str | None = Field(
        default=None,
        description=(
            "Optional starting ref for 'create'. Defaults to HEAD. Ignored "
            "for other actions."
        ),
    )
    force: bool = Field(
        default=False,
        description=(
            "If true, delete is forced (unmerged commits OK). Force-delete "
            "is destructive and requires `confirm=True`; without it the tool "
            "returns GIT_DESTRUCTIVE_NOT_APPROVED."
        ),
    )
    confirm: bool = Field(
        default=False,
        description=(
            "Required (=True) when `force=True` on action='delete'. Without "
            "explicit confirmation, force-delete returns "
            "GIT_DESTRUCTIVE_NOT_APPROVED. Ignored for non-force paths."
        ),
    )


class GitCheckoutArgs(_StrictModel):
    ref: str = Field(
        ...,
        min_length=1,
        description=(
            "Ref to check out — a branch name, tag, commit SHA, or 'HEAD'. "
            "Checking out a non-branch ref results in detached-HEAD state, "
            "which is flagged in result metadata."
        ),
    )


# NGT-03 index + commit + stash. Args models stay strict (`extra="forbid"`)
# so the model can't sneak unsupported flags into the call.


class GitAddArgs(_StrictModel):
    paths: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Explicit list of paths to stage, relative to the workspace root. "
            "There is no implicit 'add everything' — pass each path you want "
            "staged. Each path is workspace-boundary checked."
        ),
    )


class GitCommitArgs(_StrictModel):
    message: str = Field(
        ...,
        min_length=1,
        description=(
            "Commit message. Required and non-empty. Used verbatim as the "
            "`-m` arg to `git commit`."
        ),
    )


class GitStashArgs(_StrictModel):
    action: str = Field(
        ...,
        description=(
            "Stash action: 'push' (stash current working tree), 'list' "
            "(show stashes), 'apply' (re-apply a stash without dropping it), "
            "'pop' (apply + drop), 'drop' (delete a stash without applying — "
            "destructive, requires `confirm=True`), or 'clear' (delete all "
            "stashes — destructive, requires `confirm=True`)."
        ),
    )
    message: str | None = Field(
        default=None,
        description="Optional message for action='push'.",
    )
    index: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Optional 0-based stash index for action='apply'/'pop'/'drop'. "
            "Default is the top of the stash stack (`stash@{0}`)."
        ),
    )
    confirm: bool = Field(
        default=False,
        description=(
            "Required (=True) for destructive actions: 'drop' and 'clear'. "
            "Without explicit confirmation, those actions return "
            "GIT_DESTRUCTIVE_NOT_APPROVED. Ignored for safe actions."
        ),
    )


# NGT-04 recovery ops.


class GitResetArgs(_StrictModel):
    ref: str = Field(
        ...,
        min_length=1,
        description=("Ref to reset to (commit SHA, branch, tag, or e.g. 'HEAD~1')."),
    )
    mode: str = Field(
        default="mixed",
        description=(
            "Reset mode: 'mixed' (default; resets index, keeps working tree), "
            "'soft' (resets HEAD only, keeps index + working tree), or "
            "'hard' (resets HEAD, index, AND working tree — DESTRUCTIVE; "
            "requires `confirm=True`)."
        ),
    )
    confirm: bool = Field(
        default=False,
        description=(
            "Required (=True) for `mode='hard'`. Without explicit "
            "confirmation, hard reset returns GIT_DESTRUCTIVE_NOT_APPROVED. "
            "Ignored for soft/mixed."
        ),
    )


class GitReflogArgs(_StrictModel):
    limit: int = Field(
        default=20,
        ge=1,
        le=500,
        description="Max number of reflog entries to return (1..500).",
    )


def _scoped_path(ctx: RuntimeContext, raw_path: str | None, operation: str) -> str:
    """Resolve `raw_path` against the workspace boundary. Raises a
    `ToolRuntimeError(GIT_PATH_OUTSIDE_WORKSPACE)` if the path escapes the
    workspace — even though the underlying file-plugin resolver raises
    `POLICY_DENIED`, we re-wrap so the catalog code reflects the git
    family. Paths that are `None` or empty pass through unchanged."""

    if raw_path is None:
        return ""
    token = raw_path.strip()
    if not token:
        return ""
    try:
        return _resolve_path_lexical(ctx, token, operation=operation)
    except ToolRuntimeError as exc:
        if exc.code == "POLICY_DENIED":
            raise ToolRuntimeError(
                GIT_PATH_OUTSIDE_WORKSPACE,
                f"path resolves outside workspace: {token}",
                {"path": token},
            ) from exc
        raise


def _workspace_cwd(ctx: RuntimeContext) -> str:
    return str(resolve_git_repo_root(ctx))


def _ok_result(
    *,
    result: GitCommandResult,
    parsed: Any,
) -> dict[str, Any]:
    return {
        "command": list(result.command[1:]),
        "exit_code": result.exit_code,
        "parsed": parsed,
        "raw_stdout": result.stdout,
        "raw_stderr": result.stderr,
    }


def _require_success(result: GitCommandResult) -> None:
    if result.exit_code != 0:
        raise classify_git_failure(result)


def _h_status(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    raw_path = str(args.get("path") or ".")
    _scoped_path(ctx, raw_path, operation="read")
    result = run_git(
        ("status", "--porcelain=v2", "--branch"),
        cwd=_workspace_cwd(ctx),
    )
    _require_success(result)
    parsed = status_to_dict(parse_status_porcelain_v2(result.stdout))
    return _ok_result(result=result, parsed=parsed)


def _h_diff(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    ref_a = args.get("ref_a")
    ref_b = args.get("ref_b")
    path = args.get("path")
    staged = bool(args.get("staged", False))
    name_only = bool(args.get("name_only", False))

    cmd: list[str] = ["diff"]
    if staged:
        cmd.append("--cached")
    if name_only:
        cmd.append("--name-only")
    if ref_a:
        cmd.append(str(ref_a))
    if ref_b:
        cmd.append(str(ref_b))
    if path:
        resolved_path = _scoped_path(ctx, str(path), operation="read")
        cmd.extend(["--", resolved_path])

    result = run_git(tuple(cmd), cwd=_workspace_cwd(ctx))
    _require_success(result)
    parsed = {
        "name_only": name_only,
        "staged": staged,
        "ref_a": ref_a,
        "ref_b": ref_b,
        "path": path,
        "diff_text": result.stdout if not name_only else "",
        "changed_paths": (
            [line for line in result.stdout.splitlines() if line] if name_only else []
        ),
    }
    return _ok_result(result=result, parsed=parsed)


def _h_log(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    limit = int(args.get("limit") or DEFAULT_LOG_LIMIT)
    path = args.get("path")
    since = args.get("since")
    until = args.get("until")

    cmd: list[str] = [
        "log",
        f"--pretty=format:{LOG_PRETTY_FORMAT_ARG}",
        "-n",
        str(limit),
    ]
    if since:
        cmd.extend([f"--since={since}"])
    if until:
        cmd.extend([f"--until={until}"])
    if path:
        resolved_path = _scoped_path(ctx, str(path), operation="read")
        cmd.extend(["--", resolved_path])

    result = run_git(tuple(cmd), cwd=_workspace_cwd(ctx))
    _require_success(result)
    parsed = log_to_dict(parse_log_output(result.stdout))
    return _ok_result(result=result, parsed=parsed)


def _h_show(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    ref = str(args.get("ref") or "").strip()
    if not ref:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "ref is required",
            {"field": "ref"},
        )
    result = run_git(("show", ref), cwd=_workspace_cwd(ctx))
    _require_success(result)
    parsed = {
        "ref": ref,
        "output": result.stdout,
    }
    return _ok_result(result=result, parsed=parsed)


VALID_BRANCH_ACTIONS: tuple[str, ...] = ("list", "create", "delete")


def _h_branch(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    action = str(args.get("action") or "").strip()
    if action not in VALID_BRANCH_ACTIONS:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            f"action must be one of {VALID_BRANCH_ACTIONS}, got {action!r}",
            {"field": "action", "value": action},
        )
    name = str(args.get("name") or "").strip()
    from_ref = str(args.get("from_ref") or "").strip()
    force = bool(args.get("force", False))

    cwd = _workspace_cwd(ctx)

    if action == "list":
        result = run_git(("branch",), cwd=cwd)
        _require_success(result)
        entries = branch_list_to_dict(parse_branch_list(result.stdout))
        parsed: dict[str, Any] = {"action": "list", "branches": entries}
        return _ok_result(result=result, parsed=parsed)

    if action == "create":
        if not name:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                "`name` is required for action='create'",
                {"field": "name", "action": action},
            )
        cmd: list[str] = ["branch", name]
        if from_ref:
            cmd.append(from_ref)
        result = run_git(tuple(cmd), cwd=cwd)
        _require_success(result)
        parsed = {
            "action": "create",
            "name": name,
            "from_ref": from_ref or "HEAD",
        }
        return _ok_result(result=result, parsed=parsed)

    if not name:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "`name` is required for action='delete'",
            {"field": "name", "action": action},
        )
    confirm = bool(args.get("confirm", False))
    if force and not confirm:
        # force-delete is destructive. Require explicit `confirm=True`;
        raise ToolRuntimeError(
            GIT_DESTRUCTIVE_NOT_APPROVED,
            (
                f"force-delete of branch {name!r} is destructive; "
                "set `confirm=True` (gated by the approval flow) to proceed"
            ),
            {
                "action": "delete",
                "name": name,
                "force": True,
                "risk_tier": "approve",
                "requires_confirm": True,
            },
        )
    flag = "-D" if force else "-d"
    result = run_git(("branch", flag, name), cwd=cwd)
    _require_success(result)
    parsed = {"action": "delete", "name": name, "force": force, "confirmed": confirm}
    return _ok_result(result=result, parsed=parsed)


VALID_STASH_ACTIONS: tuple[str, ...] = (
    "push",
    "list",
    "apply",
    "pop",
    "drop",
    "clear",
)
# Stash actions that require explicit `confirm=True` because they destroy
# stash entries (or all of them) without a working-tree reflection.
_STASH_DESTRUCTIVE_ACTIONS: tuple[str, ...] = ("drop", "clear")
VALID_RESET_MODES: tuple[str, ...] = ("mixed", "soft", "hard")


def _h_add(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    raw_paths = args.get("paths") or []
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "`paths` must be a non-empty list of workspace-relative paths",
            {"field": "paths"},
        )
    resolved_paths: list[str] = []
    for raw_path in raw_paths:
        token = str(raw_path or "").strip()
        if not token:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                "`paths` entries must be non-empty strings",
                {"field": "paths"},
            )
        resolved_paths.append(_scoped_path(ctx, token, operation="write"))

    cmd: list[str] = ["add", "--"] + resolved_paths
    result = run_git(tuple(cmd), cwd=_workspace_cwd(ctx))
    _require_success(result)
    parsed = {
        "added_paths": resolved_paths,
    }
    return _ok_result(result=result, parsed=parsed)


_NOTHING_TO_COMMIT_PHRASES: tuple[str, ...] = (
    "nothing to commit",
    "no changes added to commit",
    "nothing added to commit",
)


def _is_nothing_to_commit(result: GitCommandResult) -> bool:
    combined = (result.stdout + "\n" + result.stderr).lower()
    return any(phrase in combined for phrase in _NOTHING_TO_COMMIT_PHRASES)


def _h_commit(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    message = str(args.get("message") or "").strip()
    if not message:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "commit message is required",
            {"field": "message"},
        )
    cmd: tuple[str, ...] = ("commit", "-m", message)
    result = run_git(cmd, cwd=_workspace_cwd(ctx))
    if result.exit_code != 0:
        if _is_nothing_to_commit(result):
            raise ToolRuntimeError(
                GIT_NOTHING_TO_COMMIT,
                "nothing staged to commit",
                {
                    "cwd": result.cwd,
                    "exit_code": result.exit_code,
                    "stdout": result.stdout[:500],
                    "stderr": result.stderr[:500],
                },
            )
        raise classify_git_failure(result)

    sha_probe = run_git(("rev-parse", "HEAD"), cwd=result.cwd)
    sha = sha_probe.stdout.strip() if sha_probe.exit_code == 0 else ""

    parsed = {
        "sha": sha,
        "message": message,
        "summary": result.stdout.strip().splitlines()[0]
        if result.stdout.strip()
        else "",
    }
    return _ok_result(result=result, parsed=parsed)


def _h_stash(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    action = str(args.get("action") or "").strip()
    if action not in VALID_STASH_ACTIONS:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            f"action must be one of {VALID_STASH_ACTIONS}, got {action!r}",
            {"field": "action", "value": action},
        )

    cwd = _workspace_cwd(ctx)

    if action == "push":
        message = str(args.get("message") or "").strip()
        cmd: list[str] = ["stash", "push"]
        if message:
            cmd.extend(["-m", message])
        result = run_git(tuple(cmd), cwd=cwd)
        _require_success(result)
        stdout_lower = result.stdout.lower()
        nothing_to_stash = "no local changes to save" in stdout_lower
        parsed = {
            "action": "push",
            "message": message,
            "nothing_to_stash": nothing_to_stash,
            "summary": result.stdout.strip().splitlines()[0]
            if result.stdout.strip()
            else "",
        }
        return _ok_result(result=result, parsed=parsed)

    if action == "list":
        result = run_git(("stash", "list"), cwd=cwd)
        _require_success(result)
        entries = stash_list_to_dict(parse_stash_list(result.stdout))
        parsed = {"action": "list", "stashes": entries}
        return _ok_result(result=result, parsed=parsed)

    index = args.get("index")
    confirm = bool(args.get("confirm", False))

    if action in _STASH_DESTRUCTIVE_ACTIONS and not confirm:
        raise ToolRuntimeError(
            GIT_DESTRUCTIVE_NOT_APPROVED,
            (
                f"stash action {action!r} is destructive; "
                "set `confirm=True` (gated by the approval flow) to proceed"
            ),
            {
                "action": action,
                "risk_tier": "approve",
                "requires_confirm": True,
            },
        )

    if action == "apply":
        apply_cmd: list[str] = ["stash", "apply"]
        if isinstance(index, int) and index >= 0:
            apply_cmd.append(f"stash@{{{index}}}")
        result = run_git(tuple(apply_cmd), cwd=cwd)
        if result.exit_code != 0:
            raise classify_git_failure(result)
        parsed = {
            "action": "apply",
            "ref": f"stash@{{{index}}}" if isinstance(index, int) else "stash@{0}",
        }
        return _ok_result(result=result, parsed=parsed)

    if action == "pop":
        pop_cmd: list[str] = ["stash", "pop"]
        if isinstance(index, int) and index >= 0:
            pop_cmd.append(f"stash@{{{index}}}")
        result = run_git(tuple(pop_cmd), cwd=cwd)
        if result.exit_code != 0:
            raise classify_git_failure(result)
        parsed = {
            "action": "pop",
            "ref": f"stash@{{{index}}}" if isinstance(index, int) else "stash@{0}",
        }
        return _ok_result(result=result, parsed=parsed)

    if action == "drop":
        drop_cmd: list[str] = ["stash", "drop"]
        if isinstance(index, int) and index >= 0:
            drop_cmd.append(f"stash@{{{index}}}")
        result = run_git(tuple(drop_cmd), cwd=cwd)
        if result.exit_code != 0:
            raise classify_git_failure(result)
        parsed = {
            "action": "drop",
            "ref": f"stash@{{{index}}}" if isinstance(index, int) else "stash@{0}",
            "confirmed": True,
        }
        return _ok_result(result=result, parsed=parsed)

    result = run_git(("stash", "clear"), cwd=cwd)
    if result.exit_code != 0:
        raise classify_git_failure(result)
    parsed = {"action": "clear", "confirmed": True}
    return _ok_result(result=result, parsed=parsed)


def _h_reset(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    ref = str(args.get("ref") or "").strip()
    if not ref:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "ref is required",
            {"field": "ref"},
        )
    mode = str(args.get("mode") or "mixed").strip()
    if mode not in VALID_RESET_MODES:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            f"mode must be one of {VALID_RESET_MODES}, got {mode!r}",
            {"field": "mode", "value": mode},
        )
    confirm = bool(args.get("confirm", False))
    if mode == "hard" and not confirm:
        raise ToolRuntimeError(
            GIT_DESTRUCTIVE_NOT_APPROVED,
            (
                "git.reset --hard is destructive (working tree + index are "
                "overwritten); set `confirm=True` (gated by the approval "
                "flow) to proceed"
            ),
            {
                "mode": "hard",
                "ref": ref,
                "risk_tier": "approve",
                "requires_confirm": True,
            },
        )
    cmd: tuple[str, ...] = ("reset", f"--{mode}", ref)
    result = run_git(cmd, cwd=_workspace_cwd(ctx))
    if result.exit_code != 0:
        raise classify_git_failure(result)
    parsed = {
        "ref": ref,
        "mode": mode,
        "confirmed": confirm,
    }
    return _ok_result(result=result, parsed=parsed)


def _h_reflog(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    limit = int(args.get("limit") or 20)
    result = run_git(
        ("reflog", "-n", str(limit)),
        cwd=_workspace_cwd(ctx),
    )
    _require_success(result)
    parsed = reflog_to_dict(parse_reflog_output(result.stdout))
    return _ok_result(result=result, parsed=parsed)


def _h_checkout(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    ref = str(args.get("ref") or "").strip()
    if not ref:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "ref is required",
            {"field": "ref"},
        )
    cwd = _workspace_cwd(ctx)
    result = run_git(("checkout", ref), cwd=cwd)
    if result.exit_code != 0:
        raise classify_git_failure(result)

    head_probe = run_git(("rev-parse", "--abbrev-ref", "HEAD"), cwd=cwd)
    detached = False
    current_branch = ""
    if head_probe.exit_code == 0:
        head_name = head_probe.stdout.strip()
        if head_name == "HEAD":
            detached = True
        else:
            current_branch = head_name

    parsed = {
        "ref": ref,
        "current_branch": current_branch,
        "detached_head": detached,
    }
    return _ok_result(result=result, parsed=parsed)


def _h_blame(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    path = str(args.get("path") or "").strip()
    if not path:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "path is required",
            {"field": "path"},
        )
    line = args.get("line")
    resolved_path = _scoped_path(ctx, path, operation="read")
    cmd: list[str] = ["blame", "--porcelain"]
    if isinstance(line, int) and line >= 1:
        cmd.extend(["-L", f"{line},{line}"])
    cmd.extend(["--", resolved_path])
    result = run_git(tuple(cmd), cwd=_workspace_cwd(ctx))
    _require_success(result)
    parsed = blame_to_dict(parse_blame_porcelain(result.stdout))
    return _ok_result(result=result, parsed=parsed)
