from __future__ import annotations

import os
from pathlib import Path

from ..errors import ToolRuntimeError


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
