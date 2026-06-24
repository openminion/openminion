import re
import shlex
from pathlib import Path
from typing import Iterable, Mapping, Optional

from openminion.modules.tool.contracts.model_ids import (
    MODEL_EXEC_CLEAR,
    MODEL_EXEC_KILL,
    MODEL_EXEC_LIST,
    MODEL_EXEC_PASTE,
    MODEL_EXEC_POLL,
    MODEL_EXEC_RUN,
    MODEL_EXEC_SEND_KEYS,
    MODEL_EXEC_SUBMIT,
)
from openminion.modules.tool.runtime.context import RuntimeContext
from openminion.tools.config import resolve_tool_workspace_root
from openminion.modules.brain.runtime.escalation import (
    ActionRiskTier,
)

from .constants import (
    EXEC_APPROVAL_PENDING_STATUSES,
    EXEC_ARTIFACT_THRESHOLD_BYTES,
)

_ARTIFACT_THRESHOLD_BYTES = EXEC_ARTIFACT_THRESHOLD_BYTES
_APPROVAL_PENDING_STATUSES = EXEC_APPROVAL_PENDING_STATUSES


_KEY_ALIASES = {
    "ENTER": b"\r",
    "RETURN": b"\r",
    "TAB": b"\t",
    "BACKSPACE": b"\x7f",
    "ESC": b"\x1b",
    "UP": b"\x1b[A",
    "DOWN": b"\x1b[B",
    "LEFT": b"\x1b[D",
    "RIGHT": b"\x1b[C",
    "C-C": b"\x03",
    "C-D": b"\x04",
    "C-Z": b"\x1a",
}
_DECLARED_EXEC_RISK_TIERS: dict[str, ActionRiskTier] = {
    MODEL_EXEC_RUN: "approve",
    MODEL_EXEC_POLL: "silent",
    MODEL_EXEC_SEND_KEYS: "approve",
    MODEL_EXEC_SUBMIT: "approve",
    MODEL_EXEC_PASTE: "approve",
    MODEL_EXEC_KILL: "approve",
    MODEL_EXEC_CLEAR: "approve",
    MODEL_EXEC_LIST: "silent",
}

_CANONICAL_EXECUTABLE_ALIASES: dict[str, str] = {
    "python3": "python3.11",
}


_UNSUPPORTED_REDIRECTION_HINT_TOOL = "file.list_dir"
_UNSUPPORTED_REDIRECTION_HINT_FIX = (
    "Redirections are not supported. For workspace inspection, use "
    "file.list_dir and file.read instead of shell chains. For command output, "
    "run the command directly; stdout and stderr previews are captured "
    "separately."
)
_UNSUPPORTED_COMMAND_OUTPUT_REDIRECTION_HINT_TOOL = "exec.run"
_UNSUPPORTED_COMMAND_OUTPUT_REDIRECTION_HINT_FIX = (
    "Redirections, pipes, and shell output truncation are not supported. Run the "
    "verification command directly; stdout and stderr previews are captured "
    "separately."
)
_PYTEST_EXECUTABLE_HINT_TOOL = "exec.run"
_PYTEST_EXECUTABLE_HINT_FIX = (
    "Bare `pytest` is not allowlisted. Run pytest through the allowed Python "
    "module form instead: `python -m pytest -q tests`. Do not use pipes, "
    "redirections, shell chaining, or output truncation."
)
_PACKAGE_INSTALL_HINT_TOOL = "exec.run"
_PACKAGE_INSTALL_HINT_FIX = (
    "Package-manager install commands are not allowlisted for this execution "
    "surface. Do not install the project just to verify local changes. If the "
    "task requires Python test verification, run the allowed direct command "
    "`python -m pytest -q tests` from the workspace instead."
)
_PYTHON_DISCOVERY_HINT_TOOL = "exec.run"
_PYTHON_DISCOVERY_HINT_FIX = (
    "Interpreter-discovery commands such as `which python3` are not allowlisted "
    "for this execution surface. If the task requires Python test verification, "
    "run the allowed direct command `python -m pytest -q tests` from the "
    "workspace instead. Do not probe interpreter paths or versions first."
)


def _resolve_workspace_cwd(ctx: RuntimeContext, raw_workdir: Optional[str]) -> Path:
    workspace_root = resolve_tool_workspace_root(
        env=ctx.env,
        fallback=ctx.workspace,
    )
    allowed_roots = tuple(_candidate_workdir_roots(ctx, workspace_root))
    if raw_workdir is None or not str(raw_workdir).strip():
        candidate = workspace_root
    else:
        raw_value = str(raw_workdir).strip()
        path_value = Path(raw_value).expanduser()
        if not path_value.is_absolute() and len(path_value.parts) == 1:
            candidate = _resolve_single_part_workdir(
                path_value.parts[0],
                allowed_roots=allowed_roots,
                fallback_root=workspace_root,
            )
        else:
            candidate = (
                path_value if path_value.is_absolute() else workspace_root / path_value
            )
        candidate = candidate.resolve(strict=False)

    if not any(_path_is_relative_to(candidate, root) for root in allowed_roots):
        raise ValueError("workdir must stay under workspace root or allowed path")

    if not candidate.exists() or not candidate.is_dir():
        raise ValueError("workdir does not exist or is not a directory")

    # Keep policy path checks consistent with the rest of openminion-tool runtime behavior.
    ctx.policy.ensure_path_allowed(
        str(candidate), workspace=workspace_root, operation="read"
    )
    return candidate


def _candidate_workdir_roots(
    ctx: RuntimeContext,
    workspace_root: Path,
) -> tuple[Path, ...]:
    roots: list[Path] = [workspace_root]
    raw_policy = getattr(getattr(ctx, "policy", None), "raw", {}) or {}
    raw_paths = raw_policy.get("paths", {}) if isinstance(raw_policy, Mapping) else {}
    if isinstance(raw_paths, Mapping):
        for key in ("read_allow", "write_allow"):
            raw_values = raw_paths.get(key, ()) or ()
            if isinstance(raw_values, (str, Path)):
                raw_values = (raw_values,)
            if not isinstance(raw_values, Iterable):
                continue
            for raw_value in raw_values:
                value = str(raw_value or "").strip()
                if not value:
                    continue
                if value in {".", "$workspace", "${workspace}", "{workspace}"}:
                    root = workspace_root
                else:
                    path_value = Path(value).expanduser()
                    root = (
                        path_value
                        if path_value.is_absolute()
                        else workspace_root / path_value
                    )
                roots.append(root.resolve(strict=False))

    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return tuple(deduped)


def _resolve_single_part_workdir(
    raw_name: str,
    *,
    allowed_roots: tuple[Path, ...],
    fallback_root: Path,
) -> Path:
    normalized = str(raw_name or "").strip()
    for root in allowed_roots:
        if root.name == normalized:
            return root
    for root in allowed_roots:
        candidate = (root / normalized).resolve(strict=False)
        if candidate.exists() and candidate.is_dir():
            return candidate
    return fallback_root / normalized


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _normalize_cd_prefixed_command(
    *,
    command: str,
    workdir: str | None,
) -> tuple[str, str | None]:
    raw_command = str(command or "").strip()
    if "&&" not in raw_command:
        return raw_command, workdir
    prefix, remainder = raw_command.split("&&", 1)
    try:
        argv = shlex.split(prefix.strip(), posix=True)
    except ValueError:
        return raw_command, workdir
    if len(argv) != 2 or str(argv[0]).strip() != "cd":
        return raw_command, workdir
    normalized_command = str(remainder or "").strip()
    if not normalized_command:
        return raw_command, workdir
    effective_workdir = str(workdir or "").strip() or str(argv[1]).strip()
    return normalized_command, effective_workdir or None


def _normalize_capture_redirection_suffix(command: str) -> str:
    raw_command = str(command or "").strip()
    if not raw_command:
        return raw_command
    return re.sub(r"(?:\s+2>&1)+\s*$", "", raw_command).strip()
