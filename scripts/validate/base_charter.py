#!/usr/bin/env python3
"""Validate the public root layout of the `openminion.base` package."""

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
BASE_ROOT = REPO_ROOT / "src" / "openminion" / "base"
ALLOWED_ROOT_FILES = {
    "README.md",
    "__init__.py",
    "constants.py",
    "debug.py",
    "generated_paths.py",
    "logging.py",
    "protocol.py",
    "redaction.py",
    "time.py",
    "types.py",
    "user_io.py",
}
ALLOWED_TOP_LEVEL_DIRS = {"channel", "config", "errors", "runtime"}


def validate_root_layout(root: Path = BASE_ROOT) -> list[str]:
    errors: list[str] = []
    root_files = sorted(path.name for path in root.iterdir() if path.is_file())
    unexpected_root_files = [
        name for name in root_files if name not in ALLOWED_ROOT_FILES
    ]
    if unexpected_root_files:
        errors.append(
            "Unexpected root files under base/: " + ", ".join(unexpected_root_files)
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
        errors.append("Unexpected top-level base dirs: " + ", ".join(unexpected_dirs))

    missing_dirs = sorted(ALLOWED_TOP_LEVEL_DIRS.difference(top_level_dirs))
    if missing_dirs:
        errors.append("Missing admitted base subpackages: " + ", ".join(missing_dirs))

    if not (root / "README.md").exists():
        errors.append("src/openminion/base/README.md missing")
    return errors


def main() -> int:
    errors = validate_root_layout()
    result = {
        "ok": not errors,
        "allowed_root_files": sorted(ALLOWED_ROOT_FILES),
        "admitted_subpackages": sorted(ALLOWED_TOP_LEVEL_DIRS),
    }
    emit_json_report(
        "validate/base_charter.py",
        result,
        summary=(
            ("base root", BASE_ROOT),
            ("allowed root files", len(ALLOWED_ROOT_FILES)),
            ("admitted subpackages", len(ALLOWED_TOP_LEVEL_DIRS)),
        ),
        findings=errors,
        ok_message="base root layout matches the admitted charter.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
