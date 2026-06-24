"""Shared helpers for repo package discovery in maintenance scripts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import tomllib


def module_pyproject_path(repo_root: Path, module: str) -> Path:
    """Return the canonical pyproject path for a repo module/package."""
    return repo_root / module / "pyproject.toml"


def discover_repo_modules(repo_root: Path) -> list[str]:
    """Return repo package names following the OpenMinion sibling layout."""
    modules: list[str] = []
    for entry in sorted(repo_root.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        if name != "openminion" and not name.startswith("openminion-"):
            continue
        if module_pyproject_path(repo_root, name).exists():
            modules.append(name)
    return modules


def load_pyproject_document(pyproject_path: Path) -> dict[str, Any] | None:
    """Load a pyproject document, returning None on missing/unreadable files."""
    if not pyproject_path.exists():
        return None
    try:
        return tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_project_version(repo_root: Path, module: str) -> str:
    """Return the package version from pyproject, or ``unknown``."""
    doc = load_pyproject_document(module_pyproject_path(repo_root, module))
    if not doc:
        return "unknown"
    return str(doc.get("project", {}).get("version", "unknown"))
