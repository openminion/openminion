"""Shared helpers for detecting ``asyncio.run(...)`` call sites."""

from __future__ import annotations

import ast
from pathlib import Path


def load_python_module(path: Path) -> tuple[str, ast.Module] | None:
    """Load source text and parsed module for a Python file."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return None
    return source, tree


def is_asyncio_run_call(node: ast.AST) -> bool:
    """Match ``asyncio.run(...)`` calls in attribute form."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "run":
        return False
    return isinstance(func.value, ast.Name) and func.value.id == "asyncio"
