#!/usr/bin/env python3
"""Validate root layout and required markers across `openminion.modules`."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_json_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULES_ROOT = REPO_ROOT / "src" / "openminion" / "modules"
TESTS_ROOT = REPO_ROOT / "tests"
TEST_OWNER_OVERRIDES = {
    "prompting": Path("services/prompting/test_prompting_contracts.py"),
}
ALLOWED_ROOT_FILES = {
    "__init__.py",
    "README.md",
    "base.py",
    "cli_common.py",
    "config.py",
    "constants.py",
    "providers.py",
    "paths.py",
}
REQUIRED_MARKERS = {
    "interfaces.py",
    "schemas.py",
    "contracts.py",
    "service.py",
    "config.py",
    "constants.py",
}
REQUIRED_MARKER_DIRS = {"schemas", "adapters", "contracts", "runtime", "storage"}
SHAPE_TOKENS = {
    "Shape: `template-aligned`",
    "Shape: `small-primitive`",
    "Shape: `engine-owning`",
    "Shape: `outlier`",
}
IGNORED_GENERATED_FILES = {".DS_Store"}


def _iter_subsystems(root: Path) -> list[Path]:
    return sorted(
        [p for p in root.iterdir() if p.is_dir() and p.name != "__pycache__"],
        key=lambda p: p.name,
    )


def validate_root_files(root: Path = MODULES_ROOT) -> list[str]:
    errors: list[str] = []
    root_files = sorted(
        p.name
        for p in root.glob("*")
        if p.is_file() and p.name not in IGNORED_GENERATED_FILES
    )
    unexpected = [name for name in root_files if name not in ALLOWED_ROOT_FILES]
    if unexpected:
        errors.append("Unexpected root files under modules/: " + ", ".join(unexpected))
    return errors


def validate_subsystem(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        display_path = path.relative_to(REPO_ROOT)
    except ValueError:
        display_path = path
    readme = path / "README.md"
    if not readme.exists():
        return [f"{display_path} missing README.md charter"]

    text = readme.read_text(encoding="utf-8")
    if not any(token in text for token in SHAPE_TOKENS):
        errors.append(f"{display_path}/README.md missing Shape token")

    rels = {
        p.relative_to(path).as_posix()
        for p in path.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    has_marker = any(marker in rels for marker in REQUIRED_MARKERS) or any(
        any(rel.startswith(f"{dirname}/") for rel in rels)
        for dirname in REQUIRED_MARKER_DIRS
    )
    if not has_marker:
        errors.append(f"{display_path} has no canonical module-shape markers")
    return errors


def validate_test_owner(name: str, tests_root: Path = TESTS_ROOT) -> list[str]:
    owner = tests_root / TEST_OWNER_OVERRIDES.get(name, Path(name))
    if owner.is_file():
        has_test = owner.stat().st_size > 0
    else:
        has_test = any(
            path.is_file() and path.stat().st_size for path in owner.rglob("test_*.py")
        )
    if has_test:
        return []
    try:
        display_path = owner.relative_to(REPO_ROOT)
    except ValueError:
        display_path = owner
    return [f"module {name!r} has no non-empty test owner under {display_path}"]


def main() -> int:
    errors = validate_root_files()
    subsystems = _iter_subsystems(MODULES_ROOT)
    for subsystem in subsystems:
        errors.extend(validate_subsystem(subsystem))
        errors.extend(validate_test_owner(subsystem.name))
    result = {
        "ok": not errors,
        "allowed_root_files": sorted(ALLOWED_ROOT_FILES),
        "subsystems": [path.name for path in subsystems],
    }
    emit_json_report(
        "validate/modules_shape.py",
        result,
        summary=(
            ("modules root", MODULES_ROOT),
            ("subsystems checked", len(subsystems)),
            ("test owners checked", len(subsystems)),
            ("required markers", len(REQUIRED_MARKERS) + len(REQUIRED_MARKER_DIRS)),
        ),
        findings=errors,
        ok_message="module root files and subsystem shape markers are clean.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
