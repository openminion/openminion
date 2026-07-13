#!/usr/bin/env python3
"""Validate the public root layout of the `openminion.tools` package."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_json_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = REPO_ROOT / "src" / "openminion" / "tools"
SCAN_ROOTS = [
    REPO_ROOT / "src" / "openminion",
    REPO_ROOT / "tests",
    REPO_ROOT / "pyproject.toml",
]
ALLOWED_ROOT_FILES = {
    "README.md",
    "__init__.py",
    "__main__.py",
    "config.py",
    "constants.py",
    "decorator.py",
    "env.py",
}
ALLOWED_TOP_LEVEL_DIRS = {
    "agent",
    "browser",
    "code",
    "exec",
    "fetch",
    "file",
    "git",
    "github",
    "gws",
    "host",
    "ip",
    "location",
    "memory",
    "mcp",
    "ops",
    "plan",
    "reaction",
    "search",
    "skill",
    "task",
    "time",
    "todo",
    "tool_catalog",
    "tool_authoring",
    "utility",
    "weather",
}
MULTI_PROVIDER_LAYOUT = {
    "browser": {"pinchtab", "playwright"},
    "fetch": {"scrapling"},
    "search": {"brave", "firecrawl", "serpapi", "serper", "tavily"},
    "weather": {"openmeteo", "weatherapi"},
}
RETIRED_PROVIDER_DIRS = (
    "browser_pinchtab",
    "browser_playwright",
    "fetch_scrapling",
    "search_brave",
    "search_firecrawl",
    "search_serpapi",
    "search_serper",
    "search_tavily",
    "weather_openmeteo",
    "weather_weatherapi",
)
LEGACY_PATH_TOKENS = {
    *(f"openminion.tools.{name}" for name in RETIRED_PROVIDER_DIRS),
    *(f"tools/{name}" for name in RETIRED_PROVIDER_DIRS),
}


def validate_root_layout(root: Path = TOOLS_ROOT) -> list[str]:
    errors: list[str] = []
    root_files = sorted(path.name for path in root.iterdir() if path.is_file())
    unexpected_root_files = [
        name for name in root_files if name not in ALLOWED_ROOT_FILES
    ]
    if unexpected_root_files:
        errors.append(
            "Unexpected root files under tools/: " + ", ".join(unexpected_root_files)
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
        errors.append("Unexpected top-level tool dirs: " + ", ".join(unexpected_dirs))

    for category, expected_providers in MULTI_PROVIDER_LAYOUT.items():
        providers_root = root / category / "providers"
        if not (providers_root / "__init__.py").exists():
            errors.append(
                f"{providers_root.relative_to(REPO_ROOT)} missing __init__.py"
            )
            continue
        discovered = {path.name for path in providers_root.iterdir() if path.is_dir()}
        missing = sorted(expected_providers.difference(discovered))
        if missing:
            errors.append(
                f"{providers_root.relative_to(REPO_ROOT)} missing providers: {', '.join(missing)}"
            )

    if not (root / "fetch" / "providers" / "core_http.py").exists():
        errors.append("src/openminion/tools/fetch/providers/core_http.py missing")

    for retired in RETIRED_PROVIDER_DIRS:
        if (root / retired).exists():
            errors.append(f"Legacy flat provider dir still present: {retired}")
    return errors


def scan_text_file(path: Path) -> list[str]:
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
                f"{path.relative_to(REPO_ROOT)}:{line}: legacy tool path token {token}"
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
        "multi_provider_categories": sorted(MULTI_PROVIDER_LAYOUT),
        "legacy_token_count": len(LEGACY_PATH_TOKENS),
    }
    emit_json_report(
        "validate/tools_layout.py",
        result,
        summary=(
            ("tools root", TOOLS_ROOT),
            ("scan roots", len(SCAN_ROOTS)),
            ("multi-provider categories", len(MULTI_PROVIDER_LAYOUT)),
            ("legacy path tokens", len(LEGACY_PATH_TOKENS)),
        ),
        findings=errors,
        ok_message="tools root layout and retired provider tokens are clean.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
