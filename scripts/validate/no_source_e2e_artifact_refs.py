#!/usr/bin/env python3
"""Validate source code does not embed E2E/generated artifact paths."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_json_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "src" / "openminion"

FORBIDDEN_SOURCE_ARTIFACT_PATHS = (
    "artifacts/cli-chat-e2e",
    ".openminion/runtime/cli-chat-e2e",
    ".openminion/runtime/skill-complex",
)


def _display_path(path: Path, root: Path) -> str:
    for base in (REPO_ROOT, root):
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            continue
    return path.as_posix()


def validate_source_e2e_artifact_refs(root: Path = SOURCE_ROOT) -> list[str]:
    findings: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in {".py", ".md", ".json", ".toml"}:
            continue
        if "__pycache__" in path.parts:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(lines, start=1):
            for forbidden in FORBIDDEN_SOURCE_ARTIFACT_PATHS:
                if forbidden in line:
                    findings.append(
                        f"{_display_path(path, root)}:{line_no}: {forbidden}"
                    )
    return findings


def main() -> int:
    findings = validate_source_e2e_artifact_refs()
    payload = {
        "ok": not findings,
        "forbidden_source_artifact_paths": list(FORBIDDEN_SOURCE_ARTIFACT_PATHS),
        "findings": findings,
    }
    emit_json_report(
        "validate_no_source_e2e_artifact_refs",
        payload,
        summary=(
            ("source root", SOURCE_ROOT),
            ("forbidden path patterns", len(FORBIDDEN_SOURCE_ARTIFACT_PATHS)),
            ("findings", len(findings)),
        ),
        findings=findings,
        ok_message="source tree is free of forbidden E2E/generated artifact paths.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
