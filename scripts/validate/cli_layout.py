#!/usr/bin/env python3
"""Validate the public root layout of the `openminion.cli` package."""

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
CLI_ROOT = REPO_ROOT / "src" / "openminion" / "cli"
SCAN_ROOTS = (
    REPO_ROOT / "src" / "openminion",
    REPO_ROOT / "tests",
    REPO_ROOT / "pyproject.toml",
)
ALLOWED_ROOT_FILES = {
    "README.md",
    "__init__.py",
    "config.py",
    "constants.py",
    "main.py",
}
ALLOWED_TOP_LEVEL_DIRS = {
    "bootstrap",
    "chat",
    "commands",
    "identity",
    "parser",
    "presentation",
    "status",
    "theme",
    "transport",
    "tui",
    "ux",
}
GROUPED_MODULES = {
    "bootstrap": {"loader", "paths"},
    "parser": {"base", "flags", "contracts"},
    "identity": {"provenance", "sync"},
    "transport": {"daemon_client"},
    "presentation": {"styles"},
}
GROUPED_LAYOUT = {
    dirname: {f"{name}.py" for name in names}
    for dirname, names in GROUPED_MODULES.items()
}
LEGACY_PATH_TOKENS = {
    f"openminion.cli.{name}" for names in GROUPED_MODULES.values() for name in names
}
TOKEN_EXEMPT_FILES = {
    REPO_ROOT / "src" / "openminion" / "cli" / "__init__.py",
    REPO_ROOT / "src" / "openminion" / "cli" / "parser" / "contracts.py",
    REPO_ROOT / "tests" / "cli" / "test_cli_layout_characterization.py",
}
RETIRED_FLAT_FILES = {
    f"{name}.py" for names in GROUPED_MODULES.values() for name in names
}


def validate_root_layout(root: Path = CLI_ROOT) -> list[str]:
    errors: list[str] = []
    root_files = sorted(path.name for path in root.iterdir() if path.is_file())
    unexpected_root_files = [
        name for name in root_files if name not in ALLOWED_ROOT_FILES
    ]
    if unexpected_root_files:
        errors.append(
            "Unexpected root files under cli/: " + ", ".join(unexpected_root_files)
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
        errors.append("Unexpected top-level CLI dirs: " + ", ".join(unexpected_dirs))

    for dirname, expected_files in GROUPED_LAYOUT.items():
        pkg_root = root / dirname
        if not (pkg_root / "__init__.py").exists():
            errors.append(f"{pkg_root.relative_to(REPO_ROOT)} missing __init__.py")
            continue
        discovered = {path.name for path in pkg_root.iterdir() if path.is_file()}
        missing = sorted(expected_files.difference(discovered))
        if missing:
            errors.append(
                f"{pkg_root.relative_to(REPO_ROOT)} missing files: {', '.join(missing)}"
            )

    for retired in RETIRED_FLAT_FILES:
        if (root / retired).exists():
            errors.append(f"Legacy flat CLI helper still present at root: {retired}")
    return errors


def scan_text_file(path: Path) -> list[str]:
    if path in TOKEN_EXEMPT_FILES:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    errors: list[str] = []
    for token in LEGACY_PATH_TOKENS:
        idx = text.find(token)
        while idx != -1:
            line = text.count("\n", 0, idx) + 1
            errors.append(
                f"{path.relative_to(REPO_ROOT)}:{line}: legacy CLI path token {token}"
            )
            idx = text.find(token, idx + 1)
    return errors


def main() -> int:
    errors = validate_root_layout()
    for scan_root in SCAN_ROOTS:
        paths = (
            [scan_root]
            if scan_root.is_file()
            else [p for p in scan_root.rglob("*") if p.is_file()]
        )
        for path in paths:
            if path.suffix == ".pyc":
                continue
            errors.extend(scan_text_file(path))
    result = {
        "ok": not errors,
        "allowed_root_files": sorted(ALLOWED_ROOT_FILES),
        "grouped_dirs": sorted(GROUPED_LAYOUT),
        "legacy_token_count": len(LEGACY_PATH_TOKENS),
    }
    emit_json_report(
        "validate/cli_layout.py",
        result,
        summary=(
            ("cli root", CLI_ROOT),
            ("scan roots", len(SCAN_ROOTS)),
            ("grouped dirs", len(GROUPED_LAYOUT)),
            ("legacy path tokens", len(LEGACY_PATH_TOKENS)),
        ),
        findings=errors,
        ok_message="cli root layout and legacy token scan are clean.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
