#!/usr/bin/env python3
"""Validate service layout, retired paths, and single-owner stack budgets."""

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
OWNER_BASELINE = REPO_ROOT / "scripts" / "baselines" / "services_owner_layout.tsv"
ALLOWED_ROOT_FILES = {"__init__.py", "config.py", "constants.py"}
MOVED_MODULES = {
    "config_bootstrap",
    "debug",
    "migration",
    "onboarding",
    "owner_status",
    "paths",
    "request_orchestrator",
    "self_improvement",
    "sidecars",
    "skill_harness",
    "vector_sync",
}
RETIRED_IMPORT_PATHS = {
    "openminion.services.agent.identity",
    "openminion.services.agent.lifecycle",
    "openminion.services.brain.cli",
    "openminion.services.channel.policy",
    "openminion.services.context.budget",
    "openminion.services.context.cleanup",
    "openminion.services.context.slices",
    "openminion.services.cron.constants",
    "openminion.services.cron.delivery",
    "openminion.services.cron.interfaces",
    "openminion.services.cron.scheduling",
    "openminion.services.integration",
    "openminion.services.lifecycle.prompts",
    "openminion.services.runtime.config",
    "openminion.services.runtime.daytona",
    "openminion.services.security.blast_radius.adapter",
    "openminion.services.security.untrusted_content",
    "openminion.services.stats.formatting",
    "openminion.services.stats.service",
    "openminion.services.stats.token_usage",
    "openminion.services.stats.types",
}
RETIRED_FILES = {
    path.removeprefix("openminion.services.").replace(".", "/") + ".py"
    for path in RETIRED_IMPORT_PATHS
    if not path.endswith((".integration", ".daytona"))
}
RETIRED_PACKAGES = {"integration", "runtime/daytona"}
OWNER_STACK_FILES = {
    "security": {
        "__init__.py",
        "blast_radius/__init__.py",
        "blast_radius/wiring.py",
        "policy.py",
        "tool_execution.py",
        "validate.py",
    },
    "stats": {"__init__.py"},
    "tool": {"__init__.py", "exposure.py", "selection.py"},
    "runtime/plugins": {
        "__init__.py",
        "discovery.py",
        "hook_runner.py",
        "hooks.py",
        "manifests.py",
        "metadata.py",
        "registry.py",
        "validate.py",
    },
}
SCAN_ROOTS = [REPO_ROOT / "src", REPO_ROOT / "tests", REPO_ROOT / "pyproject.toml"]
LEGACY_IMPORT_PATHS = RETIRED_IMPORT_PATHS | {
    f"openminion.services.{name}" for name in MOVED_MODULES
}
DIRECT_PATTERNS = {
    path: re.compile(rf"{re.escape(path)}(?![A-Za-z0-9_])")
    for path in LEGACY_IMPORT_PATHS
}
PACKAGE_IMPORT_PATTERNS = {
    name: re.compile(rf"from\s+openminion\.services\s+import\s+{name}(?:\s|$)")
    for name in MOVED_MODULES
    | {path.split(".")[2] for path in RETIRED_IMPORT_PATHS if path.count(".") == 2}
}


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _scan_text_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    errors: list[str] = []
    for dotted_path, pattern in DIRECT_PATTERNS.items():
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            errors.append(
                f"{_display_path(path)}:{line}: legacy import/string uses {dotted_path}"
            )
    for name, pattern in PACKAGE_IMPORT_PATTERNS.items():
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            errors.append(
                f"{_display_path(path)}:{line}: legacy package import uses "
                f"from openminion.services import {name}"
            )
    return errors


def _load_budgets(path: Path) -> dict[str, tuple[int, int]]:
    budgets: dict[str, tuple[int, int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        scope, max_files, max_loc = line.split("\t")
        budgets[scope] = (int(max_files), int(max_loc))
    return budgets


def _python_shape(root: Path) -> tuple[int, int]:
    files = sorted(root.rglob("*.py")) if root.exists() else []
    loc = sum(len(path.read_text(encoding="utf-8").splitlines()) for path in files)
    return len(files), loc


def validate_layout(
    *,
    services_root: Path = SERVICES_ROOT,
    scan_roots: list[Path] = SCAN_ROOTS,
    baseline_path: Path = OWNER_BASELINE,
) -> list[str]:
    errors: list[str] = []
    root_py_files = sorted(path.name for path in services_root.glob("*.py"))
    unexpected_root = [name for name in root_py_files if name not in ALLOWED_ROOT_FILES]
    if unexpected_root:
        errors.append(
            "Unexpected flat service files at root: " + ", ".join(unexpected_root)
        )

    for relative in sorted(RETIRED_FILES):
        if (services_root / relative).exists():
            errors.append(f"Retired service module exists: {relative}")
    for relative in sorted(RETIRED_PACKAGES):
        package = services_root / relative
        if package.exists() and any(package.rglob("*.py")):
            errors.append(f"Retired service package contains Python files: {relative}")

    for scope, allowed in OWNER_STACK_FILES.items():
        root = services_root / scope
        actual = (
            {str(path.relative_to(root)) for path in root.rglob("*.py")}
            if root.exists()
            else set()
        )
        unexpected = sorted(actual - allowed)
        if unexpected:
            errors.append(
                f"Duplicate {scope} owner stack files: " + ", ".join(unexpected)
            )

    for scope, (max_files, max_loc) in _load_budgets(baseline_path).items():
        relative = scope.removeprefix("services").lstrip("/")
        root = services_root / relative if relative else services_root
        files, loc = _python_shape(root)
        if files > max_files:
            errors.append(
                f"{scope} Python file ratchet increased: {files} > {max_files}"
            )
        if loc > max_loc:
            errors.append(f"{scope} LOC ratchet increased: {loc} > {max_loc}")

    for root in scan_roots:
        paths = (
            [root]
            if root.is_file()
            else [
                path
                for path in root.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts
            ]
        )
        for path in paths:
            if path.suffix != ".pyc":
                errors.extend(_scan_text_file(path))
    return errors


def main() -> int:
    errors = validate_layout()
    result = {
        "ok": not errors,
        "allowed_root_files": sorted(ALLOWED_ROOT_FILES),
        "moved_modules": sorted(MOVED_MODULES),
        "owner_stack_scopes": sorted(OWNER_STACK_FILES),
        "retired_import_paths": sorted(RETIRED_IMPORT_PATHS),
    }
    emit_json_report(
        "validate/services_layout.py",
        result,
        summary=(
            ("services root", SERVICES_ROOT),
            ("scan roots", len(SCAN_ROOTS)),
            ("retired paths", len(RETIRED_IMPORT_PATHS)),
            ("owner stacks", len(OWNER_STACK_FILES)),
        ),
        findings=errors,
        ok_message="services layout, retired paths, owner stacks, and budgets are clean.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
