#!/usr/bin/env python3
"""Audit `asyncio.run(...)` usage outside approved boundary functions."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common.asyncio_calls import is_asyncio_run_call, load_python_module  # noqa: E402

_BOUNDARY_FUNCTION_NAMES = frozenset({"main", "run", "_run"})
_NEARBY_PROXIMITY_LINES = 50


def _enclosing_function(tree: ast.Module, target_line: int) -> str:
    """Find the innermost enclosing function name at target_line."""
    candidate = "<module>"
    candidate_line = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            if start <= target_line <= end and start >= candidate_line:
                candidate = node.name
                candidate_line = start
    return candidate


def _has_nearby_pattern(
    source_lines: list[str], target_line: int, patterns: tuple[str, ...]
) -> bool:
    """Check whether any of the patterns appears within ±_NEARBY_PROXIMITY_LINES."""
    lo = max(0, target_line - _NEARBY_PROXIMITY_LINES - 1)
    hi = min(len(source_lines), target_line + _NEARBY_PROXIMITY_LINES)
    window = "\n".join(source_lines[lo:hi])
    return any(pat in window for pat in patterns)


def audit_file(path: Path, source_root: Path) -> list[dict[str, Any]]:
    """Return one row per `asyncio.run(...)` call found in path."""
    loaded = load_python_module(path)
    if loaded is None:
        return []
    source, tree = loaded

    source_lines = source.splitlines()
    rel_path = path.relative_to(source_root)
    rows: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not is_asyncio_run_call(node):
            continue
        line = node.lineno
        enclosing = _enclosing_function(tree, line)
        rows.append(
            {
                "file": str(rel_path),
                "line": line,
                "enclosing_function": enclosing,
                "is_under_cli": str(rel_path).startswith("openminion/cli/")
                or str(rel_path).startswith("cli/"),
                "is_boundary_named_function": enclosing in _BOUNDARY_FUNCTION_NAMES,
                "has_run_until_complete_nearby": _has_nearby_pattern(
                    source_lines,
                    line,
                    ("loop.run_until_complete", "run_until_complete"),
                ),
                "has_get_event_loop_nearby": _has_nearby_pattern(
                    source_lines, line, ("asyncio.get_event_loop", "get_event_loop")
                ),
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    if not src_root.is_dir():
        print(f"src/ not found at {src_root}", file=sys.stderr)
        return 1
    rows: list[dict[str, Any]] = []
    for py_file in sorted(src_root.rglob("*.py")):
        rows.extend(audit_file(py_file, src_root))
    json.dump(rows, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
