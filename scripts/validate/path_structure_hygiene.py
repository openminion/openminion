#!/usr/bin/env python3
"""Guard against path shapes that drift away from the readable-tree discipline."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

_terminal_output = importlib.import_module("scripts.common.terminal_output")
emit_json_report = _terminal_output.emit_json_report

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "src" / "openminion"
EXEMPT_FILENAMES = {"__init__.py", "__main__.py"}
DEPRECATED_DIR_NAMES = {
    "parsing": "parser",
    "focus_terminal": "terminal",
    "knowledge_graphs": "knowledge",
}
REDUNDANT_SUFFIX_RULES = {
    "_runtime": "use runtime.py inside a runtime/ owner or promote the runtime concern into a runtime/ folder",
    "_events": "use events.py inside an events/ or diagnostics/ owner instead of repeating the role in the filename",
    "_support": "prefer an explicit owner name or a real subsystem folder over *_support.py",
    "_helpers": "prefer an explicit owner name over *_helpers.py",
}
REDUNDANT_REPO_PREFIX = "openminion_"


def _relative(path: Path, root: Path = SOURCE_ROOT) -> str:
    return path.relative_to(root).as_posix()


def _parent_prefix_matches(path: Path) -> bool:
    parent = path.parent.name
    if not parent:
        return False
    stem_tokens = path.stem.split("_")
    if len(stem_tokens) <= 1:
        return False
    parent_tokens = {parent}
    if parent.endswith("s") and len(parent) > 1:
        parent_tokens.add(parent[:-1])
    return stem_tokens[0] in parent_tokens


def validate_source_tree(root: Path = SOURCE_ROOT) -> list[str]:
    findings: list[str] = []
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts:
            continue
        if path.is_dir():
            replacement = DEPRECATED_DIR_NAMES.get(path.name)
            if replacement is not None:
                findings.append(
                    f"{_relative(path, root)}/: deprecated folder name '{path.name}'; use '{replacement}/'"
                )
            continue
        if path.suffix != ".py" or path.name in EXEMPT_FILENAMES:
            continue
        rel = _relative(path, root)
        stem = path.stem
        if stem.startswith(REDUNDANT_REPO_PREFIX):
            findings.append(
                f"{rel}: redundant repo prefix '{REDUNDANT_REPO_PREFIX}' in source filename"
            )
        for suffix, guidance in REDUNDANT_SUFFIX_RULES.items():
            if stem.endswith(suffix):
                findings.append(f"{rel}: redundant suffix '{suffix}'; {guidance}")
        if _parent_prefix_matches(path):
            findings.append(
                f"{rel}: filename repeats the parent owner; let the folder carry subsystem context"
            )
    return findings


def main() -> int:
    findings = validate_source_tree()
    result = {
        "ok": not findings,
        "deprecated_dir_names": dict(sorted(DEPRECATED_DIR_NAMES.items())),
        "redundant_suffixes": sorted(REDUNDANT_SUFFIX_RULES),
        "scan_root": str(SOURCE_ROOT),
    }
    emit_json_report(
        "validate/path_structure_hygiene.py",
        result,
        summary=(
            ("source root", SOURCE_ROOT),
            ("deprecated dir names", len(DEPRECATED_DIR_NAMES)),
            ("redundant suffixes", len(REDUNDANT_SUFFIX_RULES)),
        ),
        findings=findings,
        ok_message="source path structure hygiene is clean.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
