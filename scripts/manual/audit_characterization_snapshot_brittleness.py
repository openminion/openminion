#!/usr/bin/env python3
"""Audit characterization-style tests for brittle snapshot patterns.

Heuristics (deliberately conservative — the audit reports *candidates*
for manual review; it does not auto-downgrade):

- ``snapshot``-keyword in `assert` statements (literal frozen output).
- Multi-line dict/list literal compared with `==` inside an `assert`.
- ``assert ... == <multi-line raw string>`` patterns (long expected
  blobs).
- ``assert <module>.__all__ == [...]`` (frozen export list — usually
  better expressed as `set(...).issuperset(...)`).

Outputs a JSON report with per-file finding counts so a reviewer can
pick the highest-leverage targets for a manual downgrade pass.

This script is informational and always exits 0.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from common.terminal_output import emit_json_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = REPO_ROOT / "tests"


def _is_audit_support_test(path: Path) -> bool:
    return path.parent.name == "scripts" and path.name.startswith("test_audit_")


def _is_long_string_constant(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and ("\n" in node.value or len(node.value) > 200)
    )


def _is_large_collection_literal(node: ast.expr) -> bool:
    if isinstance(node, ast.Dict):
        return len(node.keys) >= 5
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return len(node.elts) >= 5
    return False


def _scan_file(path: Path) -> dict[str, int]:
    counts = {
        "long_string_eq_compare": 0,
        "large_collection_eq_compare": 0,
        "module_all_eq_compare": 0,
        "snapshot_keyword": 0,
    }
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return counts
    if re.search(r"\bsnapshot\b", source, flags=re.IGNORECASE):
        counts["snapshot_keyword"] += 1
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return counts
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        test = node.test
        if not isinstance(test, ast.Compare) or not test.ops:
            continue
        if not isinstance(test.ops[0], ast.Eq):
            continue
        if not test.comparators:
            continue
        right = test.comparators[0]
        left = test.left
        if _is_long_string_constant(right):
            counts["long_string_eq_compare"] += 1
        if _is_large_collection_literal(right):
            counts["large_collection_eq_compare"] += 1
        if (
            isinstance(left, ast.Attribute)
            and left.attr == "__all__"
            and isinstance(right, (ast.List, ast.Tuple))
        ):
            counts["module_all_eq_compare"] += 1
    return counts


def audit() -> dict[str, object]:
    files: list[dict[str, object]] = []
    totals = {
        "long_string_eq_compare": 0,
        "large_collection_eq_compare": 0,
        "module_all_eq_compare": 0,
        "snapshot_keyword": 0,
    }
    characterization_paths = [
        path
        for path in sorted(TESTS_ROOT.rglob("test_*characterization*.py"))
        if not _is_audit_support_test(path)
    ]
    for path in characterization_paths:
        counts = _scan_file(path)
        if any(counts.values()):
            rel = path.relative_to(REPO_ROOT).as_posix()
            files.append({"path": rel, **counts})
            for k, v in counts.items():
                totals[k] += v
    return {
        "characterization_files_scanned": len(characterization_paths),
        "files_with_findings": len(files),
        "totals": totals,
        "files": files,
    }


def main(argv: list[str] | None = None) -> int:
    del argv
    report = audit()
    emit_json_report(
        "audit_characterization_snapshot_brittleness",
        report,
        summary=(
            (
                "characterization files scanned",
                report["characterization_files_scanned"],
            ),
            ("files with findings", report["files_with_findings"]),
        ),
        findings=[
            (
                f"{entry['path']}: "
                f"snapshot_keyword={entry['snapshot_keyword']}, "
                f"long_string_eq_compare={entry['long_string_eq_compare']}, "
                f"large_collection_eq_compare={entry['large_collection_eq_compare']}, "
                f"module_all_eq_compare={entry['module_all_eq_compare']}"
            )
            for entry in report["files"]
        ],
        ok_message="no characterization snapshot-brittleness candidates detected.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    # Informational only — never fail the build.
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
