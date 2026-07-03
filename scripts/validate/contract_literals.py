#!/usr/bin/env python3
"""Reject contract-version literals outside canonical definition owners."""

from __future__ import annotations
import sys

import ast
import re
from pathlib import Path
from typing import Any

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.policy import load_quality_policy  # noqa: E402
from scripts.common.terminal_output import emit_plain_findings  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [
    REPO_ROOT / "src" / "openminion" / "modules",
    REPO_ROOT / "src" / "openminion" / "tools",
    REPO_ROOT / "src" / "openminion" / "services",
    REPO_ROOT / "src" / "openminion" / "cli",
    REPO_ROOT / "src" / "openminion" / "api",
]


def _load_policy() -> dict[str, Any]:
    policy = load_quality_policy().get("contract_literals", {})
    if not isinstance(policy, dict):
        raise SystemExit("contract_literals policy must be an object")
    return policy


_POLICY = _load_policy()
VERSION_CONST_SUFFIX_RE = re.compile(
    str(
        _POLICY.get(
            "version_constant_suffix_pattern",
            r"(?:CONTRACT|INTERFACE|RENDER|SCHEMA|PROTOCOL)_VERSION$",
        )
    )
)
ALLOWLIST_FILES: set[str] = set(_POLICY.get("allowlist_files", []))
ALLOWLIST_DIR_PARTS: set[str] = set(_POLICY.get("allowlist_dir_parts", []))


def _should_scan(path: Path) -> bool:
    if not path.is_file() or path.suffix != ".py":
        return False
    # Allow files named interfaces.py or contracts.py
    if path.name in {"interfaces.py", "contracts.py"}:
        return False
    # Allow files in a contracts/ or interfaces/ directory anywhere in path
    for part in path.parts:
        if part in ALLOWLIST_DIR_PARTS:
            return False
    rel = str(path.relative_to(REPO_ROOT))
    if rel in ALLOWLIST_FILES:
        return False
    return True


def _scan_file(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, OSError):
        return []

    hits = []
    for node in ast.iter_child_nodes(tree):
        # Only check module-level assignments
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            name = target.id
            # Skip private constants (leading underscore)
            if name.startswith("_"):
                continue
            if not VERSION_CONST_SUFFIX_RE.search(name):
                continue
            # Check that the value is a string literal
            if not isinstance(node.value, ast.Constant) or not isinstance(
                node.value.value, str
            ):
                continue
            hits.append(
                f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {name} = {node.value.value!r}"
            )
    return hits


def main() -> int:
    hits: list[str] = []
    for scan_dir in SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for path in scan_dir.rglob("*.py"):
            if not _should_scan(path):
                continue
            hits.extend(_scan_file(path))

    if hits:
        emit_plain_findings(
            "Contract/interface version literals outside definition files:",
            hits,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
