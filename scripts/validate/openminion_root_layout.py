#!/usr/bin/env python3
"""Validate the OpenMinion package root layout."""

from __future__ import annotations

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
OPENMINION_ROOT = REPO_ROOT / "src" / "openminion"

ALLOWED_ROOT_FILES = {
    "__init__.py",
    "__main__.py",
    "daemon.py",
    "daemon_main.py",
}

ALLOWED_TOP_LEVEL_DIRS = {
    "api",
    "base",
    "cli",
    "modules",
    "services",
    "tools",
}


def validate_root_layout(root: Path = OPENMINION_ROOT) -> list[str]:
    errors: list[str] = []
    if not root.exists():
        return [f"OpenMinion package root missing at {root}"]

    root_files = sorted(path.name for path in root.iterdir() if path.is_file())
    unexpected_files = [name for name in root_files if name not in ALLOWED_ROOT_FILES]
    if unexpected_files:
        errors.append(
            "Unexpected files under src/openminion/: " + ", ".join(unexpected_files)
        )

    top_level_dirs = sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    )
    unexpected_dirs = [
        name for name in top_level_dirs if name not in ALLOWED_TOP_LEVEL_DIRS
    ]
    if unexpected_dirs:
        errors.append(
            "Unexpected top-level src/openminion dirs: " + ", ".join(unexpected_dirs)
        )

    missing_dirs = sorted(ALLOWED_TOP_LEVEL_DIRS.difference(top_level_dirs))
    if missing_dirs:
        errors.append(
            "Missing required src/openminion dirs: " + ", ".join(missing_dirs)
        )

    return errors


def main() -> int:
    errors = validate_root_layout()
    result = {
        "ok": not errors,
        "allowed_root_files": sorted(ALLOWED_ROOT_FILES),
        "allowed_top_level_dirs": sorted(ALLOWED_TOP_LEVEL_DIRS),
    }
    emit_json_report(
        "validate/openminion_root_layout.py",
        result,
        summary=(
            ("package root", OPENMINION_ROOT),
            ("allowed root files", len(ALLOWED_ROOT_FILES)),
            ("allowed top-level dirs", len(ALLOWED_TOP_LEVEL_DIRS)),
        ),
        findings=errors,
        ok_message="package root layout matches the canonical owner families.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
