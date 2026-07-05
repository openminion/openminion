#!/usr/bin/env python3
"""Validate the public root layout of the `openminion.services` package."""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_json_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = REPO_ROOT / "src" / "openminion" / "services"
ALLOWED_ROOT_FILES = {"__init__.py", "config.py", "constants.py"}
MOVED_MODULES = {
    "config_bootstrap",
    "migration",
    "debug",
    "onboarding",
    "owner_status",
    "paths",
    "request_orchestrator",
    "self_improvement",
    "sidecars",
    "skill_harness",
    "vector_sync",
}
SCAN_ROOTS = [REPO_ROOT / "src", REPO_ROOT / "tests", REPO_ROOT / "pyproject.toml"]
DIRECT_PATTERNS = {
    name: re.compile(rf"openminion\\.services\\.{name}(?!\\.)")
    for name in MOVED_MODULES
}
PACKAGE_IMPORT_PATTERNS = {
    name: re.compile(rf"from\\s+openminion\\.services\\s+import\\s+{name}(?:\\s|$)")
    for name in MOVED_MODULES
}


def _scan_text_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    errors: list[str] = []
    for name, pattern in DIRECT_PATTERNS.items():
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            errors.append(
                f"{path.relative_to(REPO_ROOT)}:{line}: legacy import/string uses openminion.services.{name}"
            )
    for name, pattern in PACKAGE_IMPORT_PATTERNS.items():
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            errors.append(
                f"{path.relative_to(REPO_ROOT)}:{line}: legacy package import uses from openminion.services import {name}"
            )
    return errors


def main() -> int:
    errors: list[str] = []
    root_py_files = sorted(p.name for p in SERVICES_ROOT.glob("*.py"))
    unexpected_root = [name for name in root_py_files if name not in ALLOWED_ROOT_FILES]
    if unexpected_root:
        errors.append(
            "Unexpected flat service files at root: " + ", ".join(unexpected_root)
        )

    for root in SCAN_ROOTS:
        paths = (
            [root]
            if root.is_file()
            else [
                p
                for p in root.rglob("*")
                if p.is_file() and "__pycache__" not in p.parts
            ]
        )
        for path in paths:
            if path.suffix == ".pyc":
                continue
            errors.extend(_scan_text_file(path))

    result = {
        "ok": not errors,
        "allowed_root_files": sorted(ALLOWED_ROOT_FILES),
        "moved_modules": sorted(MOVED_MODULES),
    }
    emit_json_report(
        "validate/services_layout.py",
        result,
        summary=(
            ("services root", SERVICES_ROOT),
            ("scan roots", len(SCAN_ROOTS)),
            ("moved modules", len(MOVED_MODULES)),
        ),
        findings=errors,
        ok_message="services root layout and legacy import scan are clean.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
