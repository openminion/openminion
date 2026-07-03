#!/usr/bin/env python3
"""Advisory detector for pass-through wrappers that add little local behavior."""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import sys

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.policy import load_quality_policy  # noqa: E402
from scripts.common.terminal_output import emit_plain_findings  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_ROOT = REPO_ROOT / "src" / "openminion"


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    function: str
    target: str


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts = [func.attr]
        value = func.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
        return ".".join(reversed(parts))
    return "<dynamic>"


def _is_simple_forwarder(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.Call | None:
    body = [
        stmt
        for stmt in node.body
        if not (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        )
    ]
    if len(body) != 1:
        return None
    stmt = body[0]
    if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Call):
        return stmt.value
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return stmt.value
    return None


def scan(source_root: Path) -> list[Finding]:
    policy = load_quality_policy().get("pass_through_advisory", {})
    excluded = (
        set(policy.get("excluded_files", [])) if isinstance(policy, dict) else set()
    )
    findings: list[Finding] = []
    for path in sorted(source_root.rglob("*.py")):
        try:
            rel = path.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            rel = path.as_posix()
        if rel in excluded:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            call = _is_simple_forwarder(node)
            if call is None:
                continue
            findings.append(Finding(rel, node.lineno, node.name, _call_name(call)))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--top", type=int, default=25)
    args = parser.parse_args(argv)
    findings = scan(args.source_root)
    by_file: dict[str, int] = defaultdict(int)
    for finding in findings:
        by_file[finding.path] += 1
    rendered = [
        f"{path}: {count} simple forwarding function(s)"
        for path, count in sorted(
            by_file.items(), key=lambda item: (-item[1], item[0])
        )[: args.top]
    ]
    if rendered:
        emit_plain_findings(
            "ADVISORY: pass-through wrapper hotspots:",
            rendered,
        )
    else:
        print("ADVISORY: no pass-through wrapper hotspots found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
