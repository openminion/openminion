#!/usr/bin/env python3
"""Reject direct `load_config` usage outside the ConfigManager boundary."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOT = REPO_ROOT / "src" / "openminion"
ALLOWLIST = {
    SCAN_ROOT / "base" / "config_manager.py",
}
PATTERN = "openminion.base.config.load_config"


def _imports_base_load_config(text: str, path: Path) -> bool:
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "openminion.base.config":
            for alias in node.names:
                if alias.name == "load_config":
                    return True
    return False


def main() -> int:
    violations: list[str] = []
    if not SCAN_ROOT.exists():
        print(f"SKIP: {SCAN_ROOT} does not exist.")
        return 0
    for path in SCAN_ROOT.rglob("*.py"):
        if path in ALLOWLIST:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            violations.append(f"{path}: {exc}")
            continue
        if PATTERN in text or _imports_base_load_config(text, path):
            violations.append(str(path))

    if violations:
        print("Direct base load_config usage found:")
        for item in sorted(set(violations)):
            print(f"- {item}")
        return 1

    print("OK: no direct base load_config usage outside ConfigManager.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
