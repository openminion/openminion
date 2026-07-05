#!/usr/bin/env python3
"""Validate emitted termination-reason literals against the canonical vocabulary."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_json_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "openminion"
CONSTANTS_PATH = SRC_ROOT / "services" / "agent" / "constants.py"
SCAN_ROOTS = (
    SRC_ROOT / "services" / "agent",
    SRC_ROOT / "services" / "brain",
    SRC_ROOT / "modules" / "brain",
)

METADATA_KEY = "tool_loop_termination_reason"


def _load_vocabulary() -> set[str]:
    namespace: dict[str, object] = {}
    source = CONSTANTS_PATH.read_text(encoding="utf-8")
    exec(compile(source, str(CONSTANTS_PATH), "exec"), namespace)
    values = namespace.get("TERMINATION_REASON_VALUES")
    if not isinstance(values, frozenset):
        raise SystemExit(
            "validate_termination_reason_vocabulary: TERMINATION_REASON_VALUES "
            "missing or wrong type in constants.py"
        )
    return set(values)


def _is_termination_reason_keyword(node: ast.keyword) -> bool:
    return node.arg == "termination_reason"


def _is_termination_metadata_key(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value == METADATA_KEY


def _scan_file(path: Path) -> list[dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    findings: list[dict[str, str]] = []
    rel = path.relative_to(REPO_ROOT).as_posix()
    for node in ast.walk(tree):
        # Function-call kwarg form: foo(..., termination_reason="...")
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if not _is_termination_reason_keyword(kw):
                    continue
                if isinstance(kw.value, ast.Constant) and isinstance(
                    kw.value.value, str
                ):
                    findings.append(
                        {
                            "path": rel,
                            "line": str(kw.value.lineno),
                            "value": kw.value.value,
                            "shape": "kwarg",
                        }
                    )
        # Dict-literal form: {"tool_loop_termination_reason": "..."}
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    key is not None
                    and _is_termination_metadata_key(key)
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    findings.append(
                        {
                            "path": rel,
                            "line": str(value.lineno),
                            "value": value.value,
                            "shape": "metadata_dict",
                        }
                    )
    return findings


def _scan_tree() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if path == CONSTANTS_PATH:
                # Skip the constants file itself — it defines the
                # vocabulary, not emit sites.
                continue
            out.extend(_scan_file(path))
    out.sort(key=lambda f: (f["path"], int(f["line"])))
    return out


def audit() -> dict[str, object]:
    vocabulary = _load_vocabulary()
    discovered = _scan_tree()
    drift = [
        {
            **finding,
            "detail": (
                f"value {finding['value']!r} not in "
                "TERMINATION_REASON_VALUES; add a TERMINATION_REASON_* "
                "constant + frozenset entry in "
                "services/agent/constants.py"
            ),
        }
        for finding in discovered
        if finding["value"] not in vocabulary
    ]
    return {
        "vocabulary_size": len(vocabulary),
        "emit_sites_scanned": len(discovered),
        "ok": not drift,
        "findings": drift,
    }


def main() -> int:
    report = audit()
    emit_json_report(
        "validate_termination_reason_vocabulary",
        report,
        summary=(
            ("vocabulary size", report["vocabulary_size"]),
            ("emit sites scanned", report["emit_sites_scanned"]),
            ("findings", len(report["findings"])),
        ),
        findings=[
            f"{finding['path']}:{finding['line']}: {finding['detail']}"
            for finding in report["findings"]
        ],
        ok_message="termination-reason vocabulary is clean.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
