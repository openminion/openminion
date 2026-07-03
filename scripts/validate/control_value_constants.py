#!/usr/bin/env python3
"""Flag repeated control-value literals that should move to constants."""

from __future__ import annotations
import sys

import argparse
import ast
import collections
import pathlib

REPO_IMPORT_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.policy import load_quality_policy  # noqa: E402
from scripts.common.terminal_output import emit_plain_findings  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCAN_DIRS = [
    ROOT / "src" / "openminion" / "modules",
    ROOT / "src" / "openminion" / "tools",
    ROOT / "src" / "openminion" / "services",
    ROOT / "src" / "openminion" / "cli",
    ROOT / "src" / "openminion" / "api",
]


def _load_policy() -> dict[str, object]:
    policy = load_quality_policy().get("control_value_constants", {})
    if not isinstance(policy, dict):
        raise SystemExit("control_value_constants policy must be an object")
    return policy


_POLICY = _load_policy()
MIN_STRING_LEN = int(_POLICY.get("min_string_len", 3))
MIN_OCCURRENCES = int(_POLICY.get("min_occurrences", 3))
EXCLUDED_LITERALS: set[str] = set(_POLICY.get("excluded_literals", []))


def _is_test_file(path: pathlib.Path) -> bool:
    return "tests" in path.parts or path.name.startswith("test_")


def _should_scan(path: pathlib.Path) -> bool:
    return path.is_file() and path.suffix == ".py" and not _is_test_file(path)


class StringLiteralCollector(ast.NodeVisitor):
    """Collect string literals used in comparison/membership/match contexts."""

    def __init__(self) -> None:
        self.counts: collections.Counter[str] = collections.Counter()

    def _record(self, value: str) -> None:
        if len(value) < MIN_STRING_LEN:
            return
        low = value.lower()
        if low in EXCLUDED_LITERALS or value in EXCLUDED_LITERALS:
            return
        self.counts[value] += 1

    def visit_Compare(self, node: ast.Compare) -> None:
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant) and isinstance(
                comparator.value, str
            ):
                self._record(comparator.value)
        if isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
            self._record(node.left.value)
        self.generic_visit(node)

    def visit_MatchValue(self, node: ast.MatchValue) -> None:
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            self._record(node.value.value)
        self.generic_visit(node)


def _scan_file(path: pathlib.Path) -> list[tuple[str, int]]:
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, OSError):
        return []
    collector = StringLiteralCollector()
    collector.visit(tree)
    return [
        (literal, count)
        for literal, count in collector.counts.items()
        if count >= MIN_OCCURRENCES
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Exit non-zero if any violations found (default: warn mode, exit 0).",
    )
    args = parser.parse_args()

    hits: list[str] = []
    for scan_dir in SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for path in scan_dir.rglob("*.py"):
            if not _should_scan(path):
                continue
            violations = _scan_file(path)
            for literal, count in sorted(violations):
                rel = str(path.relative_to(ROOT))
                hits.append(
                    f"{rel}: string {literal!r} appears {count}x in comparisons — "
                    f"consider a named constant"
                )

    if hits:
        prefix = "WARNING" if not args.strict else "ERROR"
        emit_plain_findings(
            f"{prefix}: control-value string literals without named constants:",
            hits,
        )
        if args.strict:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
