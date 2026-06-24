#!/usr/bin/env python3
"""Reject reintroductions of removed context-reset symbols."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from common.terminal_output import emit_json_report  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOT = REPO_ROOT / "src" / "openminion"

# Each entry is (symbol_name, suggested_replacement_for_error_message).
RETIRED_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("BudgetReport", "TokenBudgetReport"),
    (
        "BudgetSectionUsage",
        "(deleted; identity-section data lives on IdentityBudgetResult)",
    ),
    ("ContextDraft", "(deleted; build_pack inlines the policy call)"),
    ("ContextPackPolicy", "(deleted; build_pack inlines the policy call)"),
    ("get_slice_v15", "get_slice"),
    ("make_compat_budget_report", "(deleted; no replacement)"),
    ("register_pack_policy", "(deleted; no replacement)"),
    ("get_pack_policy", "(deleted; no replacement)"),
    ("pack_policy_names", "(deleted; no replacement)"),
)

# Allowed to mention the removed names.
ALLOWED_REL_PATHS = frozenset(
    {
        "scripts/manual/context_reset.py",
    }
)


def _iter_py_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in ALLOWED_REL_PATHS:
            continue
        yield path


_RETIRED_REPLACEMENTS = dict(RETIRED_SYMBOLS)


class _SymbolCollector(ast.NodeVisitor):
    """Walk the AST and record removed-symbol references in actual code
    (not comments, docstrings, or other string literals)."""

    def __init__(self) -> None:
        self.findings: list[tuple[int, str]] = []

    def _check(self, name: str, lineno: int) -> None:
        if name in _RETIRED_REPLACEMENTS:
            self.findings.append((lineno, name))

    def visit_Name(self, node: ast.Name) -> None:
        self._check(node.id, node.lineno)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._check(node.attr, node.lineno)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._check(node.name, node.lineno)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check(node.name, node.lineno)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self._check(alias.name, node.lineno)
            if alias.asname:
                self._check(alias.asname, node.lineno)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            # appear; if a removed symbol shows up as a module-name leaf,
            # flag it.
            for part in alias.name.split("."):
                self._check(part, node.lineno)
        self.generic_visit(node)


def _scan_file(path: Path) -> list[tuple[int, str, str, str]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if not any(symbol in text for symbol in _RETIRED_REPLACEMENTS):
        return []
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    collector = _SymbolCollector()
    collector.visit(tree)
    findings: list[tuple[int, str, str, str]] = []
    if collector.findings:
        lines = text.splitlines()
        for line_no, symbol in collector.findings:
            replacement = _RETIRED_REPLACEMENTS[symbol]
            line_text = lines[line_no - 1].rstrip() if 0 < line_no <= len(lines) else ""
            findings.append((line_no, symbol, replacement, line_text))
    return findings


def main() -> int:
    if not SCAN_ROOT.exists():
        print(f"error: scan root not found: {SCAN_ROOT}", file=sys.stderr)
        return 2

    report_lines: list[str] = []
    scanned_files = 0
    for path in _iter_py_files(SCAN_ROOT):
        scanned_files += 1
        file_findings = _scan_file(path)
        if not file_findings:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        for line_no, symbol, replacement, line_text in file_findings:
            report_lines.append(
                f"{rel}:{line_no}: removed symbol {symbol!r} reappeared "
                f"(use {replacement!r}): {line_text}"
            )
    payload = {
        "ok": not report_lines,
        "scan_root": str(SCAN_ROOT),
        "files_scanned": scanned_files,
        "findings": report_lines,
    }
    emit_json_report(
        "context_reset",
        payload,
        summary=(("files scanned", scanned_files), ("findings", len(report_lines))),
        findings=report_lines,
        ok_message="removed context-reset symbols remain absent.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 0 if not report_lines else 1


if __name__ == "__main__":
    raise SystemExit(main())
