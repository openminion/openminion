from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import ToolRuntimeError
from openminion.tools.config import resolve_tool_workspace_root, workspace_retry_path

if TYPE_CHECKING:
    from .context import RuntimeContext


def _expand_path_pair(value: str, workspace: Path) -> tuple[Path, Path]:
    expanded = value.replace("${WORKSPACE}", str(workspace))
    candidate = Path(expanded).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate

    candidate_abs = Path(os.path.abspath(candidate))
    resolved = candidate_abs.resolve(strict=False)
    return candidate_abs, resolved


def _is_subpath(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_candidate_path(raw_path: str, workspace: Path) -> tuple[Path, Path]:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate

    candidate_abs = Path(os.path.abspath(candidate))

    try:
        resolved = candidate_abs.resolve(strict=False)
    except RuntimeError as exc:  # pragma: no cover - extremely rare cycles
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            f"Unable to resolve path '{raw_path}' due to symlink loop",
            {"path": raw_path},
        ) from exc

    return candidate_abs, resolved


def resolve_workspace_root(ctx: "RuntimeContext") -> Path:
    runtime_env = getattr(ctx, "env", None) or {}
    explicit_workspace = str(
        runtime_env.get("OPENMINION_WORKSPACE_ROOT", "") or ""
    ).strip() or str(runtime_env.get("OPENMINION_WORKSPACE", "") or "").strip()
    if explicit_workspace:
        return Path(explicit_workspace).expanduser().resolve(strict=False)

    raw = getattr(ctx.policy, "raw", {})
    workspace_root = raw.get("workspace_root")
    if workspace_root:
        return Path(workspace_root).expanduser().resolve(strict=False)

    env_workspace = resolve_tool_workspace_root(env=runtime_env, fallback="")
    return (
        env_workspace
        if env_workspace != Path.cwd().resolve(strict=False)
        else Path(ctx.workspace).expanduser().resolve(strict=False)
    )


def resolve_relative_base_dir(ctx: "RuntimeContext") -> Path:
    workspace_root = resolve_workspace_root(ctx)
    raw = getattr(ctx.policy, "raw", {})
    context_metadata = raw.get("context_metadata", {}) if isinstance(raw, dict) else {}
    candidate = str(context_metadata.get("cwd", "") or "").strip()
    if not candidate:
        return workspace_root
    resolved_candidate = Path(candidate).expanduser().resolve(strict=False)
    return (
        resolved_candidate
        if resolved_candidate.is_relative_to(workspace_root)
        else workspace_root
    )


def _workspace_escape_error(raw_path: str, workspace_root: Path) -> ToolRuntimeError:
    retry_path = workspace_retry_path(raw_path)
    return ToolRuntimeError(
        "POLICY_DENIED",
        (
            f"path escapes workspace root: {raw_path}. "
            f"Use a relative path under the workspace root, for example {retry_path}."
        ),
        details={
            "workspace_root": str(workspace_root),
            "retry_path": retry_path,
            "retry_hint": "Use a relative path under the workspace root.",
        },
    )


def resolve_path(ctx: "RuntimeContext", raw_path: str, operation: str) -> str:
    workspace_root = resolve_workspace_root(ctx)
    relative_base_dir = resolve_relative_base_dir(ctx)
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        parts = candidate.parts
        if parts and parts[0] == workspace_root.name:
            candidate = Path(*parts[1:]) if len(parts) > 1 else Path(".")
        candidate = relative_base_dir / candidate
    resolved = candidate.resolve(strict=False)

    if not Path(raw_path).expanduser().is_absolute():
        try:
            resolved.relative_to(workspace_root)
        except ValueError:
            raise _workspace_escape_error(raw_path, workspace_root)

    try:
        ctx.policy.ensure_path_allowed(
            str(resolved),
            workspace=workspace_root,
            operation=operation,
        )
    except ToolRuntimeError as exc:
        details = exc.details if isinstance(exc.details, dict) else {}
        if details.get("rule") == f"paths.{operation}_allow":
            raise _workspace_escape_error(raw_path, workspace_root) from exc
        raise
    return str(resolved)
