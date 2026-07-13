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
from scripts.validate.base_charter import validate_upward_imports  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULES_DIR = REPO_ROOT / "src" / "openminion" / "modules"
SERVICES_DIR = REPO_ROOT / "src" / "openminion" / "services"
BASE_DIR = REPO_ROOT / "src" / "openminion" / "base"
LEGACY_API_NAMESPACE = "".join(["openminion", ".services", ".api", "."])
FORBIDDEN_MODULE_IMPORTS = [
    re.compile(r"^\s*from\s+openminion\.services\.", re.MULTILINE),
    re.compile(r"^\s*import\s+openminion\.services\.", re.MULTILINE),
    re.compile(r"^\s*from\s+openminion\.api\.", re.MULTILINE),
    re.compile(r"^\s*import\s+openminion\.api\.", re.MULTILINE),
]
FORBIDDEN_SERVICE_IMPORTS = [
    re.compile(r"^\s*from\s+openminion\.api\.", re.MULTILINE),
    re.compile(r"^\s*import\s+openminion\.api\.", re.MULTILINE),
]
FORBIDDEN_BASE_CONFIG_LOAD = [
    re.compile(
        r"^\s*from\s+openminion\.base\.config\s+import\s+.*\bload_config\b",
        re.MULTILINE,
    ),
    re.compile(r"openminion\.base\.config\.load_config", re.MULTILINE),
]


def scan_file(path: pathlib.Path, *, layer: str | None = None) -> list[str]:
    try:
        rel = str(path.relative_to(REPO_ROOT))
    except ValueError:
        rel = str(path)
    resolved_layer = layer
    if resolved_layer is None:
        if path.is_relative_to(MODULES_DIR):
            resolved_layer = "modules"
        elif path.is_relative_to(SERVICES_DIR):
            resolved_layer = "services"
        else:
            raise ValueError(f"cannot infer import layer for {path}")
    text = path.read_text(encoding="utf-8", errors="ignore")
    hits = []
    patterns = (
        FORBIDDEN_MODULE_IMPORTS
        if resolved_layer == "modules"
        else FORBIDDEN_SERVICE_IMPORTS
    )
    for pattern in patterns:
        for m in pattern.finditer(text):
            line_no = text[: m.start()].count("\n") + 1
            hits.append(f"{rel}:{line_no}: {m.group(0).strip()}")
    if resolved_layer == "modules":
        for pattern in FORBIDDEN_BASE_CONFIG_LOAD:
            for m in pattern.finditer(text):
                line_no = text[: m.start()].count("\n") + 1
                hits.append(f"{rel}:{line_no}: {m.group(0).strip()}")
    return hits


def scan_base(root: pathlib.Path = BASE_DIR) -> list[str]:
    return validate_upward_imports(root)


def main() -> int:
    hits: list[str] = []
    for file in MODULES_DIR.rglob("*.py"):
        hits.extend(scan_file(file))
    for file in SERVICES_DIR.rglob("*.py"):
        hits.extend(scan_file(file))
    hits.extend(scan_base())
    if hits:
        emit_plain_findings(
            "Forbidden imports detected (modules -> services/api/base config load or services -> api):",
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
