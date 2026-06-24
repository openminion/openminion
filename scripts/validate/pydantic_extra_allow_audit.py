#!/usr/bin/env python3
"""Audit `extra=\"allow\"` declarations against the checked-in allowlist."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_json_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "openminion"
ALLOWLIST_TSV = (
    REPO_ROOT / "scripts" / "baselines" / "pydantic_extra_allow_allowlist.tsv"
)


@dataclass(frozen=True)
class _Decl:
    path: str
    line: int
    model_name: str


@dataclass(frozen=True)
class _AllowlistEntry:
    path: str
    line: int
    model_name: str
    reason: str


@dataclass
class _AuditReport:
    decls: list[_Decl] = field(default_factory=list)
    allowlist: list[_AllowlistEntry] = field(default_factory=list)
    findings: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def _load_allowlist(path: Path) -> list[_AllowlistEntry]:
    out: list[_AllowlistEntry] = []
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        rel_path, line_no, model_name, reason = parts
        try:
            out.append(
                _AllowlistEntry(
                    path=rel_path,
                    line=int(line_no),
                    model_name=model_name,
                    reason=reason,
                )
            )
        except ValueError:
            continue
    return out


def _is_extra_allow_call(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    for kw in node.keywords:
        if (
            kw.arg == "extra"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value == "allow"
        ):
            return True
    return False


def _enclosing_class_name(tree: ast.Module, target_line: int) -> str:
    best: tuple[int, str] = (-1, "<module>")
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        start = node.lineno
        end = getattr(node, "end_lineno", start) or start
        if start <= target_line <= end and start > best[0]:
            best = (start, node.name)
    return best[1]


def _scan_file(path: Path) -> list[_Decl]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    out: list[_Decl] = []
    rel_path = path.relative_to(REPO_ROOT).as_posix()
    for node in ast.walk(tree):
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if value is None or not _is_extra_allow_call(value):
            continue
        out.append(
            _Decl(
                path=rel_path,
                line=value.lineno,
                model_name=_enclosing_class_name(tree, value.lineno),
            )
        )
    return out


def _scan_tree(root: Path) -> list[_Decl]:
    out: list[_Decl] = []
    for path in sorted(root.rglob("*.py")):
        out.extend(_scan_file(path))
    out.sort(key=lambda d: (d.path, d.line))
    return out


def audit(
    *, src_root: Path = SRC_ROOT, allowlist_path: Path = ALLOWLIST_TSV
) -> _AuditReport:
    report = _AuditReport(
        decls=_scan_tree(src_root), allowlist=_load_allowlist(allowlist_path)
    )
    allow_index: dict[tuple[str, int], _AllowlistEntry] = {
        (entry.path, entry.line): entry for entry in report.allowlist
    }
    decl_keys = {(decl.path, decl.line) for decl in report.decls}

    for decl in report.decls:
        key = (decl.path, decl.line)
        entry = allow_index.get(key)
        if entry is None:
            report.findings.append(
                {
                    "code": "unjustified_extra_allow",
                    "path": decl.path,
                    "line": str(decl.line),
                    "model": decl.model_name,
                    "detail": (
                        'extra="allow" without allowlist row; add a '
                        "one-line justification to "
                        f"{allowlist_path.relative_to(REPO_ROOT).as_posix()}."
                    ),
                }
            )
            continue
        if entry.model_name != decl.model_name:
            report.findings.append(
                {
                    "code": "model_name_mismatch",
                    "path": decl.path,
                    "line": str(decl.line),
                    "model": decl.model_name,
                    "detail": (
                        f"allowlist names {entry.model_name!r} but source "
                        f"declares {decl.model_name!r}; update the allowlist."
                    ),
                }
            )

    for entry in report.allowlist:
        if (entry.path, entry.line) not in decl_keys:
            report.findings.append(
                {
                    "code": "stale_allowlist_row",
                    "path": entry.path,
                    "line": str(entry.line),
                    "model": entry.model_name,
                    "detail": (
                        'allowlist row no longer matches any extra="allow" '
                        "declaration; remove or update the row."
                    ),
                }
            )
    return report


def main(argv: list[str] | None = None) -> int:
    _ = argv
    report = audit()
    payload = {
        "checked": len(report.decls),
        "allowlisted": len(report.allowlist),
        "ok": report.ok,
        "findings": report.findings,
    }
    emit_json_report(
        "validate_pydantic_extra_allow_audit",
        payload,
        summary=(
            ("checked declarations", len(report.decls)),
            ("allowlist rows", len(report.allowlist)),
            ("findings", len(report.findings)),
        ),
        findings=[
            f"{finding['code']}: {finding['path']}:{finding['line']} ({finding['model']}) — {finding['detail']}"
            for finding in report.findings
        ],
        ok_message='all extra="allow" declarations match the checked-in allowlist.',
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
