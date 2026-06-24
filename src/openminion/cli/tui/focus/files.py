from __future__ import annotations

import os
from pathlib import Path

DEFAULT_MAX_FILES = 5000
DEFAULT_MAX_DEPTH = 6
DEFAULT_MAX_FILE_SIZE = 1_048_576  # 1 MB

_DEFAULT_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".next",
        "dist",
        "build",
        "target",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".gradle",
        ".idea",
        ".vscode",
        ".cache",
        "out",
    }
)


def build_file_index(
    working_dir: str | os.PathLike[str],
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    ignore_dirs: frozenset[str] | set[str] | None = None,
) -> list[tuple[str, str]]:
    """Return alphabetized ``(relative_path, absolute_path)`` file pairs."""

    ignore_dirs = _DEFAULT_IGNORE_DIRS if ignore_dirs is None else ignore_dirs

    root = Path(working_dir).expanduser()
    try:
        if not root.is_dir():
            return []
        root_resolved = root.resolve(strict=False)
    except OSError:
        return []

    results: list[tuple[str, str]] = []
    stack: list[tuple[Path, int]] = [(root_resolved, 0)]

    while stack and len(results) < max_files:
        current, depth = stack.pop()
        try:
            entries = sorted(
                current.iterdir(),
                key=lambda p: p.name,
            )
        except (PermissionError, OSError):
            continue

        for entry in entries:
            if len(results) >= max_files:
                break
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if entry.name in ignore_dirs:
                        continue
                    if depth + 1 > max_depth:
                        continue
                    stack.append((entry, depth + 1))
                    continue
                if not entry.is_file():
                    continue
                try:
                    if entry.stat().st_size > max_file_size:
                        continue
                except OSError:
                    continue
            except OSError:
                continue

            try:
                rel = entry.resolve(strict=False).relative_to(root_resolved)
            except (ValueError, OSError):
                continue
            rel_str = str(rel).replace(os.sep, "/")
            results.append((rel_str, str(entry)))

    results.sort(key=lambda pair: pair[0])
    return results


__all__ = [
    "DEFAULT_MAX_FILES",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_FILE_SIZE",
    "build_file_index",
]
