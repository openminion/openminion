#!/usr/bin/env python3
"""Guard modules against forbidden service, API, and config imports."""

from __future__ import annotations
import sys

import pathlib
import re

REPO_IMPORT_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_plain_findings  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULES_DIR = REPO_ROOT / "src" / "openminion" / "modules"
LEGACY_API_NAMESPACE = "".join(["openminion", ".services", ".api", "."])
APPROVED_SERVICE_PATHS = (
    "openminion.services.cron.",
    "openminion.services.cron",
)
FORBIDDEN = [
    re.compile(r"^\s*from\s+openminion\.services\.", re.MULTILINE),
    re.compile(r"^\s*import\s+openminion\.services\.", re.MULTILINE),
    re.compile(r"^\s*from\s+openminion\.api\.", re.MULTILINE),
    re.compile(r"^\s*import\s+openminion\.api\.", re.MULTILINE),
]

# Approved cross-layer service imports. These files deliberately bridge modules
# and services as part of their defined architectural role. Each entry must have
# a rationale comment.
EXCLUDED_MODULE_FILES: set[str] = {
    "src/openminion/modules/tool/executor.py",
    "src/openminion/modules/tool/cli/runtime.py",
    "src/openminion/modules/brain/tools/action_dispatch.py",
    "src/openminion/modules/controlplane/adapters/client.py",
}
FORBIDDEN_BASE_CONFIG_LOAD = [
    re.compile(
        r"^\s*from\s+openminion\.base\.config\s+import\s+.*\bload_config\b",
        re.MULTILINE,
    ),
    re.compile(r"openminion\.base\.config\.load_config", re.MULTILINE),
]


def scan_file(path: pathlib.Path) -> list[str]:
    rel = str(path.relative_to(REPO_ROOT))
    if rel in EXCLUDED_MODULE_FILES:
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    hits = []
    for pattern in FORBIDDEN:
        for m in pattern.finditer(text):
            # Approved shared-service paths are exempt — modules may consume
            # them as infrastructure.
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.end())
            if line_end == -1:
                line_end = len(text)
            line_content = text[line_start:line_end]
            if any(approved in line_content for approved in APPROVED_SERVICE_PATHS):
                continue
            line_no = text[: m.start()].count("\n") + 1
            hits.append(f"{rel}:{line_no}: {m.group(0).strip()}")
    for pattern in FORBIDDEN_BASE_CONFIG_LOAD:
        for m in pattern.finditer(text):
            line_no = text[: m.start()].count("\n") + 1
            hits.append(f"{rel}:{line_no}: {m.group(0).strip()}")
    return hits


def main() -> int:
    hits: list[str] = []
    for file in MODULES_DIR.rglob("*.py"):
        hits.extend(scan_file(file))
    if hits:
        emit_plain_findings(
            "Forbidden imports detected (modules -> services/api/base config load):",
            hits,
        )
        return 1

    legacy_hits: list[str] = []
    for file in REPO_ROOT.rglob("*"):
        if not file.is_file():
            continue
        if file.suffix.lower() not in {".py", ".md", ".sh"}:
            continue
        try:
            text = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if LEGACY_API_NAMESPACE not in text:
            continue
        for match in re.finditer(re.escape(LEGACY_API_NAMESPACE), text):
            line_no = text[: match.start()].count("\n") + 1
            legacy_hits.append(
                f"{file.relative_to(REPO_ROOT)}:{line_no}: {LEGACY_API_NAMESPACE}"
            )

    if legacy_hits:
        emit_plain_findings(
            f"Legacy namespace references detected ({LEGACY_API_NAMESPACE}):",
            legacy_hits,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
