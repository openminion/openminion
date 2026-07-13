#!/usr/bin/env python3
"""Regression guard for logging control-plane centralization."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_plain_findings  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "openminion"

BASIC_CONFIG_PATTERN = re.compile(r"logging\.basicConfig\(")
SET_LEVEL_PATTERN = re.compile(r"\.setLevel\(")
GET_LOGGER_PATTERN = re.compile(r"logging\.getLogger\(")
STRUCTURED_EVENT_PATTERN = re.compile(r"format_structured_event\(")

CANONICAL_LOGGING_OWNER = Path("src/openminion/base/logging.py")
PRIORITY_STRUCTURED_FILES = {
    Path("src/openminion/daemon.py"),
    Path("src/openminion/services/runtime/daemon.py"),
}

ALLOWED_PRINT_CALL_PATH_PATTERNS = (
    re.compile(r"^src/openminion/modules/.+/cli\.py$"),
    re.compile(r"^src/openminion/modules/.+/cli_runtime\.py$"),
    re.compile(r"^src/openminion/modules/.+/cli/__init__\.py$"),
    re.compile(r"^src/openminion/modules/.+/cli/runtime\.py$"),
    re.compile(r"^src/openminion/modules/controlplane/adapters/cli_adapter\.py$"),
    re.compile(r"^src/openminion/modules/identity/controlplane/main\.py$"),
    re.compile(r"^src/openminion/modules/task/runtime/migration_runner\.py$"),
    re.compile(r"^src/openminion/cli/commands/context_cleanup\.py$"),
    re.compile(r"^src/openminion/services/runtime/cli\.py$"),
)
PRINT_SCAN_PREFIXES = (
    Path("src/openminion/services"),
    Path("src/openminion/modules"),
)


def rel(path: Path) -> Path:
    return path.relative_to(REPO_ROOT)


def iter_python_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def line_no(text: str, index: int) -> int:
    return text[:index].count("\n") + 1


def _is_allowed_print_path(rel_path: Path) -> bool:
    rendered = str(rel_path)
    return any(pattern.match(rendered) for pattern in ALLOWED_PRINT_CALL_PATH_PATTERNS)


def _scan_regex_owners(
    pattern: re.Pattern[str], *, allowed_owner: Path | None = None
) -> list[str]:
    hits: list[str] = []
    for path in iter_python_files():
        rel_path = rel(path)
        if allowed_owner is not None and rel_path == allowed_owner:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in pattern.finditer(text):
            hits.append(f"{rel_path}:{line_no(text, match.start())}: {match.group(0)}")
    return hits


def scan_basic_config_owners() -> list[str]:
    return _scan_regex_owners(
        BASIC_CONFIG_PATTERN, allowed_owner=CANONICAL_LOGGING_OWNER
    )


def scan_set_level_owners() -> list[str]:
    return _scan_regex_owners(SET_LEVEL_PATTERN, allowed_owner=CANONICAL_LOGGING_OWNER)


def scan_print_calls() -> list[str]:
    hits: list[str] = []
    for path in iter_python_files():
        rel_path = rel(path)
        if not any(
            str(rel_path).startswith(str(prefix)) for prefix in PRINT_SCAN_PREFIXES
        ):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "print":
                continue
            if _is_allowed_print_path(rel_path):
                continue
            hits.append(f"{rel_path}:{node.lineno}: print(...)")
    return hits


def scan_priority_logger_factory_usage() -> list[str]:
    hits: list[str] = []
    for rel_path in PRIORITY_STRUCTURED_FILES:
        path = REPO_ROOT / rel_path
        if not path.exists():
            hits.append(f"{rel_path}: missing priority file")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in GET_LOGGER_PATTERN.finditer(text):
            hits.append(f"{rel_path}:{line_no(text, match.start())}: {match.group(0)}")
    return hits


def scan_priority_structured_format_usage() -> list[str]:
    hits: list[str] = []
    for rel_path in PRIORITY_STRUCTURED_FILES:
        path = REPO_ROOT / rel_path
        if not path.exists():
            hits.append(f"{rel_path}: missing priority file")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if STRUCTURED_EVENT_PATTERN.search(text) is None:
            hits.append(f"{rel_path}: missing format_structured_event(...) usage")
    return hits


def main() -> int:
    failed = False

    basic_config_hits = scan_basic_config_owners()
    set_level_hits = scan_set_level_owners()
    print_hits = scan_print_calls()
    logger_factory_hits = scan_priority_logger_factory_usage()
    structured_hits = scan_priority_structured_format_usage()

    if basic_config_hits:
        failed = True
        emit_plain_findings(
            "Non-canonical logging.basicConfig() owners detected:",
            basic_config_hits,
            trailing_blank_line=True,
        )

    if set_level_hits:
        failed = True
        emit_plain_findings(
            "Non-canonical .setLevel() ownership detected:",
            set_level_hits,
            trailing_blank_line=True,
        )

    if print_hits:
        failed = True
        emit_plain_findings(
            "Non-allowlisted print(...) calls detected:",
            print_hits,
            trailing_blank_line=True,
        )

    if logger_factory_hits:
        failed = True
        emit_plain_findings(
            "Priority runtime paths must use canonical logger factory (no logging.getLogger):",
            logger_factory_hits,
            trailing_blank_line=True,
        )

    if structured_hits:
        failed = True
        emit_plain_findings(
            "Priority runtime paths missing structured event formatting:",
            structured_hits,
            trailing_blank_line=True,
        )

    if failed:
        sys.stderr.write(
            "FAIL: logging control-plane regression guard found violations.\n"
        )
        return 1

    print("OK: logging control-plane regression guard is clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
