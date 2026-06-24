#!/usr/bin/env python3
"""Reject legacy `.openminion` defaults outside allowlisted owners."""

from __future__ import annotations
import sys

from pathlib import Path
import re

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_plain_findings  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULES_DIR = REPO_ROOT / "src" / "openminion" / "modules"
PATTERNS = [
    re.compile(r"~/.openminion(?!-data)"),
    re.compile(r"/\.openminion/"),
]
ALLOWLIST_FILES = {
    "src/openminion/modules/memory/migrate.py",
    "src/openminion/modules/telemetry/service.py",
    # identity/config.py defines standalone-mode defaults for identity paths.
    # These intentionally reference ~/.openminion as a fallback when data_root
    # is not injected (module standalone mode).
    "src/openminion/modules/identity/config.py",
}


def _should_scan(path: Path) -> bool:
    if not path.is_file() or path.suffix != ".py":
        return False
    rel = str(path.relative_to(REPO_ROOT))
    if rel in ALLOWLIST_FILES:
        return False
    return True


def main() -> int:
    hits: list[str] = []
    for path in MODULES_DIR.rglob("*.py"):
        if not _should_scan(path):
            continue
        rel = str(path.relative_to(REPO_ROOT))
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in PATTERNS:
            if pattern.search(text):
                hits.append(f"{rel}: {pattern.pattern}")
    if hits:
        emit_plain_findings("Legacy .openminion defaults detected:", hits)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
