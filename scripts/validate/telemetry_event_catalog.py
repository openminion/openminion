#!/usr/bin/env python3.11
"""Validate that emitted telemetry event types stay in the registered catalog."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_json_report  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCAN_ROOT = REPO_ROOT / "src" / "openminion"
CATALOG_PATH = (
    REPO_ROOT / "src" / "openminion" / "modules" / "telemetry" / "events" / "catalog.py"
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scan-root",
        default=str(DEFAULT_SCAN_ROOT),
        help="Python source tree to scan for telemetry event_type literals.",
    )
    return parser.parse_args(argv)


def _literal_event_values(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        value = node.value.strip()
        return {value} if value else set()
    if isinstance(node, ast.IfExp):
        return _literal_event_values(node.body) | _literal_event_values(node.orelse)
    return set()


class _EventTypeCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.literals: set[str] = set()
        self._scopes: list[dict[str, set[str]]] = [{}]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scoped(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_scoped(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        values = _literal_event_values(node.value)
        if values:
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "event_type":
                    self._scopes[-1]["event_type"] = values
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        values = _literal_event_values(node.value) if node.value is not None else set()
        if (
            values
            and isinstance(node.target, ast.Name)
            and node.target.id == "event_type"
        ):
            self._scopes[-1]["event_type"] = values
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        for keyword in node.keywords:
            if keyword.arg != "event_type":
                continue
            values = _literal_event_values(keyword.value)
            if not values and isinstance(keyword.value, ast.Name):
                values = self._resolve_name(keyword.value.id)
            self.literals.update(values)
        self.generic_visit(node)

    def _resolve_name(self, name: str) -> set[str]:
        for scope in reversed(self._scopes):
            values = scope.get(name)
            if values:
                return values
        return set()

    def _visit_scoped(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
    ) -> None:
        self._scopes.append({})
        self.generic_visit(node)
        self._scopes.pop()


def _iter_python_files(root: Path):
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _collect_literals(scan_root: Path) -> set[str]:
    literals: set[str] = set()
    for path in _iter_python_files(scan_root):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        collector = _EventTypeCollector()
        collector.visit(tree)
        literals.update(collector.literals)
    return literals


def _catalog_string_assignments(tree: ast.Module) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for statement in tree.body:
        if not isinstance(statement, ast.Assign | ast.AnnAssign):
            continue
        target = (
            statement.target
            if isinstance(statement, ast.AnnAssign)
            else statement.targets[0]
        )
        value = statement.value
        if (
            isinstance(target, ast.Name)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            assignments[target.id] = value.value
    return assignments


def _event_type_node_values(node: ast.AST, assignments: dict[str, str]) -> set[str]:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "frozenset" and node.args:
            return _event_type_node_values(node.args[0], assignments)
    if not isinstance(node, ast.Set | ast.List | ast.Tuple):
        return set()

    values: set[str] = set()
    for element in node.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            values.add(element.value)
        elif isinstance(element, ast.Name) and element.id in assignments:
            values.add(assignments[element.id])
    return values


def _load_event_types() -> set[str]:
    source = CATALOG_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(CATALOG_PATH))
    assignments = _catalog_string_assignments(tree)
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
            value = statement.value
        else:
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "EVENT_TYPES"
            for target in targets
        ):
            return (
                _event_type_node_values(value, assignments)
                if value is not None
                else set()
            )
    raise RuntimeError(f"EVENT_TYPES assignment not found in {CATALOG_PATH}")


def build_summary(*, scan_root: Path) -> dict[str, object]:
    event_types = _load_event_types()
    literals = _collect_literals(scan_root)
    unregistered = sorted(literals - event_types)
    return {
        "registered": len(literals & event_types),
        "total_literals_seen": len(literals),
        "catalog_size": len(event_types),
        "unregistered": unregistered,
        "ok": not unregistered,
        "scan_root": str(scan_root),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    summary = build_summary(scan_root=Path(args.scan_root).resolve(strict=False))
    emit_json_report(
        "validate_telemetry_event_catalog",
        summary,
        summary=(
            ("scan root", summary["scan_root"]),
            ("registered", summary["registered"]),
            ("catalog size", summary["catalog_size"]),
            ("unregistered", len(summary["unregistered"])),
        ),
        findings=[
            f"unregistered event type: {value}" for value in summary["unregistered"]
        ],
        ok_message="all emitted telemetry event types are registered.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
