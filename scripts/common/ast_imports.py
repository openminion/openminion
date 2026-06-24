"""Shared AST helpers for validator import scans."""

from __future__ import annotations

import ast


def is_type_checking_guard(node: ast.If) -> bool:
    """Return whether an ``if`` node is a ``TYPE_CHECKING`` guard."""
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    return False


def collect_top_level_import_targets(tree: ast.Module) -> list[str]:
    """Collect top-level import targets while skipping nested lazy imports."""
    targets: list[str] = []

    def walk(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, ast.ImportFrom):
                if node.module is not None and node.level == 0:
                    targets.append(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    targets.append(alias.name)
            elif isinstance(node, ast.If):
                if is_type_checking_guard(node):
                    continue
                walk(node.body)
                walk(node.orelse)
            elif isinstance(node, ast.Try):
                walk(node.body)
                for handler in node.handlers:
                    walk(handler.body)
                walk(node.orelse)
                walk(node.finalbody)

    walk(tree.body)
    return targets
