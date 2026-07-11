#!/usr/bin/env python3
"""Validate that `services.agent.memory` stays thin adapter glue."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_json_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
RESIDUAL_FILES = {"__init__.py", "capsule.py", "gateway_adapter.py"}
ALIAS_OWNER_PREFIXES = (
    "openminion.modules.memory.",
    "openminion.modules.memory",
)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _import_module_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.ImportFrom):
        return node.module
    if isinstance(node, ast.Import):
        return node.names[0].name if node.names else None
    return None


def _is_module_memory_import(name: str | None) -> bool:
    return bool(name and name.startswith(ALIAS_OWNER_PREFIXES))


def _validate_alias(path: Path, tree: ast.Module, root: Path) -> list[str]:
    errors: list[str] = []
    rel = _relative(path, root)
    text = path.read_text(encoding="utf-8")
    if "Compatibility alias" not in text and "Compatibility aliases" not in text:
        errors.append(f"{rel}: compatibility alias file must say it is an alias")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            errors.append(
                f"{rel}:{node.lineno}: compatibility alias defines runtime logic `{node.name}`"
            )
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module_name = _import_module_name(node)
            if module_name == "__future__":
                continue
            if not _is_module_memory_import(module_name):
                errors.append(
                    f"{rel}:{node.lineno}: alias imports outside module memory owner: {module_name}"
                )
    return errors


def _validate_gateway(path: Path, tree: ast.Module, root: Path) -> list[str]:
    errors: list[str] = []
    rel = _relative(path, root)
    forbidden_prefix = "openminion.services.agent.memory."
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module_name = _import_module_name(node)
            if module_name and module_name.startswith(forbidden_prefix):
                errors.append(
                    f"{rel}:{node.lineno}: gateway adapter imports service memory domain owner: {module_name}"
                )
    return errors


def validate(root: Path) -> list[str]:
    memory_dir = root / "src" / "openminion" / "services" / "agent" / "memory"
    errors: list[str] = []
    if not memory_dir.is_dir():
        return [f"missing services agent memory directory: {memory_dir}"]
    for path in sorted(memory_dir.glob("*.py")):
        tree = _parse(path)
        if path.name == "gateway_adapter.py":
            errors.extend(_validate_gateway(path, tree, root))
            continue
        if path.name in RESIDUAL_FILES:
            continue
        errors.extend(_validate_alias(path, tree, root))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    errors = validate(root)
    memory_dir = root / "src" / "openminion" / "services" / "agent" / "memory"
    checked = len(list(memory_dir.glob("*.py"))) if memory_dir.exists() else 0
    result = {
        "ok": not errors,
        "checked": checked,
        "residual_files": sorted(RESIDUAL_FILES),
        "findings": errors,
    }
    emit_json_report(
        "validate/memory_boundary.py",
        result,
        summary=(
            ("services agent memory root", memory_dir),
            ("checked", checked),
            ("findings", len(errors)),
        ),
        findings=errors,
        ok_message="services.agent.memory boundary is clean.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
