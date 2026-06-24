#!/usr/bin/env python3
"""Report broad exception handler counts and silent-pass hotspots."""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class FileRow:
    path: str
    area: str
    total: int
    silent_pass: int
    continue_count: int
    log_then_continue: int
    raise_or_return: int
    other: int


def _matches_exception(node: ast.ExceptHandler) -> bool:
    typ = node.type
    if isinstance(typ, ast.Name):
        return typ.id == "Exception"
    if isinstance(typ, ast.Tuple):
        return any(
            isinstance(elt, ast.Name) and elt.id == "Exception" for elt in typ.elts
        )
    return False


def _bucket(node: ast.ExceptHandler) -> str:
    if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
        return "silent_pass"
    if any(
        isinstance(child, (ast.Continue, ast.Break))
        for child in ast.walk(ast.Module(body=node.body, type_ignores=[]))
    ):
        return "continue"
    has_log = False
    has_return_or_raise = False
    for child in ast.walk(ast.Module(body=node.body, type_ignores=[])):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            owner = child.func.value
            if isinstance(owner, ast.Name) and owner.id in {"log", "logger", "typer"}:
                has_log = True
        if isinstance(child, (ast.Return, ast.Raise)):
            has_return_or_raise = True
    if has_log and not has_return_or_raise:
        return "log_then_continue"
    if has_return_or_raise:
        return "raise_or_return"
    return "other"


def _area_for(path: Path) -> str:
    parts = path.parts
    if "src" not in parts:
        return parts[0] if parts else ""
    idx = parts.index("src")
    rel = parts[idx + 2 :]  # skip src/openminion
    if not rel:
        return ""
    if rel[0] == "modules" and len(rel) > 1:
        return f"modules/{rel[1]}"
    if rel[0] == "services" and len(rel) > 1:
        return f"services/{rel[1]}"
    if rel[0] == "tools" and len(rel) > 1:
        return f"tools/{rel[1]}"
    if rel[0] == "cli" and len(rel) > 1:
        return f"cli/{rel[1]}"
    if rel[0] == "api" and len(rel) > 1:
        return f"api/{rel[1]}"
    return rel[0]


def scan(root: Path) -> list[FileRow]:
    rows: list[FileRow] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        counts = Counter()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if not _matches_exception(node):
                continue
            counts["total"] += 1
            counts[_bucket(node)] += 1
        if counts["total"] == 0:
            continue
        rows.append(
            FileRow(
                path=path.as_posix(),
                area=_area_for(path),
                total=counts["total"],
                silent_pass=counts["silent_pass"],
                continue_count=counts["continue"],
                log_then_continue=counts["log_then_continue"],
                raise_or_return=counts["raise_or_return"],
                other=counts["other"],
            )
        )
    return rows


def _render_tsv(rows: Iterable[FileRow]) -> str:
    lines = [
        "path\tarea\ttotal\tsilent_pass\tcontinue\tlog_then_continue\traise_or_return\tother"
    ]
    for row in rows:
        lines.append(
            f"{row.path}\t{row.area}\t{row.total}\t{row.silent_pass}\t"
            f"{row.continue_count}\t{row.log_then_continue}\t{row.raise_or_return}\t{row.other}"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="src/openminion")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args()

    root = Path(args.root)
    rows = scan(root)
    totals = Counter()
    area_counts = Counter()
    for row in rows:
        totals["handlers"] += row.total
        totals["silent_pass"] += row.silent_pass
        totals["continue"] += row.continue_count
        totals["log_then_continue"] += row.log_then_continue
        totals["raise_or_return"] += row.raise_or_return
        totals["other"] += row.other
        area_counts[row.area] += row.total

    top_rows = sorted(
        rows, key=lambda row: (row.total, row.silent_pass, row.path), reverse=True
    )[: args.top]
    top_areas = area_counts.most_common(args.top)
    payload = {
        "root": root.as_posix(),
        "file_count": len(rows),
        "handler_count": totals["handlers"],
        "silent_pass_count": totals["silent_pass"],
        "shape_counts": {
            "silent_pass": totals["silent_pass"],
            "continue": totals["continue"],
            "log_then_continue": totals["log_then_continue"],
            "raise_or_return": totals["raise_or_return"],
            "other": totals["other"],
        },
        "top_areas": [{"area": area, "count": count} for area, count in top_areas],
        "top_files": [row.__dict__ for row in top_rows],
        "rows": [row.__dict__ for row in rows],
    }

    text = (
        json.dumps(payload, indent=2, sort_keys=True)
        if args.json
        else _render_tsv(rows)
    )
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
