#!/usr/bin/env python3
"""Guard source code against env-config ownership drift."""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "openminion"

DIRECT_ENV_PATTERNS = (
    re.compile(r"os\.getenv\("),
    re.compile(r"os\.environ\.get\("),
    re.compile(r"os\.environ\["),
)

FROM_SOURCES_PATTERN = re.compile(r"EnvironmentConfig\.from_sources\(")

TOOLS_ENV_IMPORT_PATTERNS = (
    re.compile(r"^\s*from\s+openminion\.tools\.env\s+import\b", re.MULTILINE),
    re.compile(r"^\s*import\s+openminion\.tools\.env\b", re.MULTILINE),
)

ALLOWED_FROM_SOURCES_FILES = {
    Path("src/openminion/api/runtime.py"),
    Path("src/openminion/base/config/io.py"),
    Path("src/openminion/base/config/manager.py"),
    Path("src/openminion/base/config/paths.py"),
    Path("src/openminion/cli/commands/interactive.py"),
    Path("src/openminion/cli/ux/deprecation.py"),
    Path("src/openminion/cli/ux/verbosity.py"),
    Path("src/openminion/cli/main.py"),
    Path("src/openminion/daemon.py"),
    Path("src/openminion/modules/tool/runtime/registry_toolspec.py"),
    Path("src/openminion/services/health/service.py"),
}

TOOLS_ENV_MODULE = Path("src/openminion/tools/env.py")
ALLOWED_TOOLS_ENV_IMPORT_FILES = {
    # IP plugin reads package-local env through the canonical tools.env facade.
    Path("src/openminion/tools/ip/plugin.py"),
    # Search plugin resolves search-provider overrides through the canonical tools.env facade.
    Path("src/openminion/tools/search/plugin.py"),
    # Tavily plugin resolves Tavily-specific env through the canonical tools.env facade.
    Path("src/openminion/tools/search/providers/tavily/plugin.py"),
    # Tavily HTTP client reads API key/url through the canonical tools.env facade.
    Path("src/openminion/tools/search/providers/tavily/search.py"),
}
DIRECT_ENV_SCAN_PREFIXES = (Path("src/openminion"),)
ALLOWED_DIRECT_ENV_FILES = {
    # Direct process-env reads require explicit approval at the file level.
    Path("src/openminion/base/channel/console.py"),
    Path("src/openminion/base/config/bootstrap.py"),
    # Subprocess env forwarding boundary: child-process inheritance is
    # intentionally centralized here so subprocess launchers do not
    # open-code parent env reads throughout runtime/tool code.
    Path("src/openminion/base/config/env/subprocess.py"),
    Path("src/openminion/base/config/paths.py"),
    Path("src/openminion/base/generated_paths.py"),
    Path("src/openminion/base/logging.py"),
    Path("src/openminion/services/runtime/env.py"),
}

# Phase-5 lock: these reviewed CLI surfaces must remain canonical and may not
# reintroduce direct process-env reads or feature-level from_sources calls.
REQUIRED_CANONICAL_CLI_FILES = {
    Path("src/openminion/cli/commands/chat.py"),
    Path("src/openminion/cli/commands/tui.py"),
    Path("src/openminion/cli/commands/agents.py"),
    Path("src/openminion/cli/commands/status/__init__.py"),
    Path("src/openminion/cli/presentation/styles.py"),
    Path("src/openminion/modules/brain/cli.py"),
}


def _rel(path: Path) -> Path:
    return path.relative_to(REPO_ROOT)


def _iter_python_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def line_no(text: str, index: int) -> int:
    return text[:index].count("\n") + 1


def _iter_pattern_hits(
    path: Path,
    text: str,
    patterns: tuple[re.Pattern[str], ...],
) -> list[str]:
    rel_path = _rel(path)
    hits: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            hits.append(
                f"{rel_path}:{line_no(text, match.start())}: {match.group(0).strip()}"
            )
    return hits


def scan_direct_env() -> list[str]:
    hits: list[str] = []
    for path in _iter_python_files():
        rel_path = _rel(path)
        if not any(
            str(rel_path).startswith(str(prefix)) for prefix in DIRECT_ENV_SCAN_PREFIXES
        ):
            continue
        if rel_path in ALLOWED_DIRECT_ENV_FILES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits.extend(_iter_pattern_hits(path, text, DIRECT_ENV_PATTERNS))
    return hits


def scan_from_sources() -> list[str]:
    hits: list[str] = []
    for path in _iter_python_files():
        rel_path = _rel(path)
        if rel_path in ALLOWED_FROM_SOURCES_FILES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits.extend(_iter_pattern_hits(path, text, (FROM_SOURCES_PATTERN,)))
    return hits


def scan_tools_env_imports() -> list[str]:
    hits: list[str] = []
    for path in _iter_python_files():
        rel_path = _rel(path)
        if rel_path == TOOLS_ENV_MODULE or rel_path in ALLOWED_TOOLS_ENV_IMPORT_FILES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits.extend(_iter_pattern_hits(path, text, TOOLS_ENV_IMPORT_PATTERNS))
    return hits


def scan_required_cli_surfaces() -> list[str]:
    hits: list[str] = []
    for rel_path in sorted(REQUIRED_CANONICAL_CLI_FILES):
        path = REPO_ROOT / rel_path
        if not path.exists():
            hits.append(f"{rel_path}: missing required canonical CLI file")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits.extend(_iter_pattern_hits(path, text, DIRECT_ENV_PATTERNS))
        hits.extend(_iter_pattern_hits(path, text, (FROM_SOURCES_PATTERN,)))
    return hits


def main() -> int:
    direct_env_hits = scan_direct_env()
    from_sources_hits = scan_from_sources()
    tools_env_hits = scan_tools_env_imports()
    required_cli_hits = scan_required_cli_surfaces()

    failed = False

    if direct_env_hits:
        failed = True
        sys.stderr.write(
            "Direct process-env reads detected outside approved boundary files:\n"
        )
        sys.stderr.write("\n".join(direct_env_hits) + "\n\n")

    if from_sources_hits:
        failed = True
        sys.stderr.write(
            "EnvironmentConfig.from_sources() detected outside approved boundary files:\n"
        )
        sys.stderr.write("\n".join(from_sources_hits) + "\n\n")

    if tools_env_hits:
        failed = True
        sys.stderr.write("Active imports of openminion.tools.env detected:\n")
        sys.stderr.write("\n".join(tools_env_hits) + "\n\n")

    if required_cli_hits:
        failed = True
        sys.stderr.write(
            "Canonical Phase-5 CLI surfaces regressed (direct env reads or from_sources):\n"
        )
        sys.stderr.write("\n".join(required_cli_hits) + "\n\n")

    if failed:
        sys.stderr.write(
            "FAIL: env/config centralization regression guard found violations.\n"
        )
        return 1

    print("OK: env/config centralization regression guard is clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
