from collections.abc import Iterable, Mapping
import re
from pathlib import Path

from openminion.modules.tool.runtime.context import RuntimeContext
from openminion.tools.config import resolve_tool_workspace_root


def _resolve_workspace_cwd(ctx: RuntimeContext, raw_workdir: str | None) -> Path:
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

    if not any(candidate.is_relative_to(root) for root in allowed_roots):
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


def _normalize_capture_redirection_suffix(command: str) -> str:
    raw_command = str(command or "").strip()
    if not raw_command:
        return raw_command
    return re.sub(r"(?:\s+2>&1)+\s*$", "", raw_command).strip()
