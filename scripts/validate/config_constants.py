#!/usr/bin/env python3
"""Reject scattered config/constants drift outside owner files."""

from __future__ import annotations

import ast
import pathlib
import re
import sys

REPO_IMPORT_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_plain_findings  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_SCAN_ROOTS = (
    ROOT / "src" / "openminion" / "modules",
    ROOT / "src" / "openminion" / "services",
    ROOT / "src" / "openminion" / "tools",
)
EXCLUDED_FILENAMES = {"config.py", "constants.py"}
NAME_PATTERN = re.compile(
    r"(^_DEFAULT_)|(^_TIMEOUT_)|(^_BUDGET_)|(_MAX($|_))|(_MIN($|_))|"
    r"(_COUNT($|_))|(_INTERVAL($|_))|(_LIMIT($|_))|(_CAP($|_))|"
    r"(_SIZE($|_))|(_THRESHOLD($|_))|(_K$)"
)
ALLOWED_NAMES = {
    "ADAPTIVE_TERM_ITERATION_CAP",
    "MICRO_CORRECTION_ANOMALY_THRESHOLD",
    "_DEFAULT_PROFILES",
    "_DEFAULT_ROUTE_DESCRIPTIONS",
    "_RAW_INTENT_EXECUTION_STATE_MAX_ITEMS",
    "_SESSION_WORK_SUMMARY_MAX_CHARS",
    "_TOOL_OUTCOME_MAX_STAGE_PER_TURN",
    "PENDING_TURN_CONTEXT_MAX_STALE_TURNS",
}


def _assignment_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Assign):
        names: list[str] = []
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.append(target.id)
        return names
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def _literal_assignment(node: ast.AST) -> bool:
    value = node.value if isinstance(node, ast.AnnAssign) else node.value
    return isinstance(value, ast.Constant) and isinstance(
        value.value, (int, float, str)
    )


def _scan_file(path: pathlib.Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"{path.relative_to(ROOT)}:{exc.lineno}: syntax error: {exc.msg}"]

    hits: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if not _literal_assignment(node):
            continue
        for name in _assignment_names(node):
            if name in ALLOWED_NAMES:
                continue
            if not NAME_PATTERN.search(name):
                continue
            hits.append(
                f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', 1)}: "
                f"{name} should live in an area config.py or constants.py owner"
            )
    return hits


def _iter_scan_paths(scan_roots: tuple[pathlib.Path, ...]) -> list[pathlib.Path]:
    candidates: list[pathlib.Path] = []
    for root in scan_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if path.name in EXCLUDED_FILENAMES:
                continue
            candidates.append(path)
    return candidates


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    scan_roots = (
        tuple((ROOT / arg).resolve() for arg in args) if args else DEFAULT_SCAN_ROOTS
    )
    hits: list[str] = []
    for path in _iter_scan_paths(scan_roots):
        hits.extend(_scan_file(path))

    if hits:
        emit_plain_findings(
            "ERROR: config/constants drift detected. Move tunable defaults to "
            "the nearest area config.py and fixed shared literals to the "
            "nearest area constants.py.",
            hits,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
