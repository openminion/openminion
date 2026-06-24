#!/usr/bin/env python3
"""Reject raw control-value strings outside canonical constants owners."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "openminion"

# Map literal -> set of allowed contexts (used by AST visitor).
OWNED_LITERALS: dict[str, str] = {
    "module_state": "STATE_KEY_MODULE_STATE",
    "finalization_status": "STATE_KEY_FINALIZATION_STATUS",
    "task_backed_resume_state": "STATE_KEY_TASK_BACKED_RESUME",
    "working_state": "STATE_KEY_WORKING",
    "next_attempt_state": "STATE_KEY_NEXT_ATTEMPT",
    "source_outcome_status": "STATE_KEY_SOURCE_OUTCOME",
    "emit_adaptive_status": "EVENT_NAME_ADAPTIVE_STATUS",
    "active_state": "STATE_KEY_ACTIVE",
}

# "hot" is too common as a standalone literal; only flag when paired with the
# key "runtime_mode" in the same dict literal. Handled via a dedicated visitor.
RUNTIME_MODE_HOT_OWNER = "RUNTIME_MODE_HOT"

# File-relative paths excluded from scanning entirely.
EXCLUDED_FILES: frozenset[str] = frozenset(
    {
        # Frozen migration DDL — table name literal is permanent.
        "modules/session/storage/migrations/versions/0001_baseline.py",
        # SQL table-existence probe matches the frozen DDL string.
        "services/health/observability.py",
    }
)


class _AllowedContextTracker(ast.NodeVisitor):
    """Track allowed parent-contexts where literal strings are not control values.

    Allowed contexts:
      - `__all__` assignment values.
      - `typing.Literal[...]` subscript members.
    """

    def __init__(self) -> None:
        # Set of (lineno, col_offset) for ast.Constant nodes inside allowed ctx.
        self.allowed: set[tuple[int, int]] = set()

    def visit_Assign(self, node: ast.Assign) -> None:
        is_all = any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        )
        if is_all:
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    self.allowed.add((sub.lineno, sub.col_offset))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # typing.Literal[...] — match `Literal` name on the value side.
        value = node.value
        name = ""
        if isinstance(value, ast.Name):
            name = value.id
        elif isinstance(value, ast.Attribute):
            name = value.attr
        if name == "Literal":
            for sub in ast.walk(node.slice):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    self.allowed.add((sub.lineno, sub.col_offset))
        self.generic_visit(node)


def _scan_file(path: Path) -> list[dict[str, object]]:
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, OSError):
        return []
    tracker = _AllowedContextTracker()
    tracker.visit(tree)
    violations: list[dict[str, object]] = []
    rel = path.relative_to(SRC).as_posix()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if (node.lineno, node.col_offset) in tracker.allowed:
            continue
        value = node.value
        if value in OWNED_LITERALS:
            violations.append(
                {
                    "file": rel,
                    "line": node.lineno,
                    "literal": value,
                    "constant": OWNED_LITERALS[value],
                }
            )
    # Detect {"runtime_mode": "hot"} dict-literal pattern.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, val in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and key.value == "runtime_mode"
                and isinstance(val, ast.Constant)
                and isinstance(val.value, str)
                and val.value == "hot"
            ):
                if (val.lineno, val.col_offset) not in tracker.allowed:
                    violations.append(
                        {
                            "file": rel,
                            "line": val.lineno,
                            "literal": 'runtime_mode="hot"',
                            "constant": RUNTIME_MODE_HOT_OWNER,
                        }
                    )
    return violations


def main() -> int:
    checked = 0
    all_violations: list[dict[str, object]] = []
    for path in SRC.rglob("*.py"):
        if path.name == "constants.py":
            continue
        rel = path.relative_to(SRC).as_posix()
        if rel in EXCLUDED_FILES:
            continue
        checked += 1
        all_violations.extend(_scan_file(path))
    ok = len(all_violations) == 0
    payload = {"checked": checked, "violations": all_violations, "ok": ok}
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
