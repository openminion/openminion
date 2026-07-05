"""Protect the canonical self-improvement metadata contract."""

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
SELF_IMPROVEMENT_PATH = SRC_ROOT / "services" / "lifecycle" / "self_improvement.py"
ALLOWED_TRIGGER_TOKEN_CONTEXTS = {
    ("ImprovementNote",),
    ("ImprovementNote", "to_dict"),
    ("ImprovementNote", "from_dict"),
    ("SelfImprovementEngine", "set_note_status"),
    ("SelfImprovementEngine", "_upsert_failure_note"),
    ("SelfImprovementEngine", "_write_markdown_note"),
    ("_build_trigger_tokens",),
}


class _TriggerTokenVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.findings: list[str] = []
        self._context_stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._context_stack.append(node.name)
        self.generic_visit(node)
        self._context_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._context_stack.append(node.name)
        self.generic_visit(node)
        self._context_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id == "trigger_tokens":
            self._record(node.lineno)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr == "trigger_tokens":
            self._record(node.lineno)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        if node.value == "trigger_tokens":
            self._record(node.lineno)
        self.generic_visit(node)

    def _record(self, lineno: int) -> None:
        context = tuple(self._context_stack)
        if (
            self.path == SELF_IMPROVEMENT_PATH
            and context in ALLOWED_TRIGGER_TOKEN_CONTEXTS
        ):
            return
        self.findings.append(
            f"{self.path}:{lineno}: forbidden trigger_tokens reference in context {context or ('<module>',)}"
        )


def _scan_trigger_tokens(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return [f"{path}:1: unable to parse file for trigger_tokens scan: {exc}"]
    visitor = _TriggerTokenVisitor(path)
    visitor.visit(tree)
    return visitor.findings


def _scan_applied_count_writes(path: Path) -> list[str]:
    findings: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return [f"{path}:1: unable to parse file for applied-count scan: {exc}"]

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Subscript):
                continue
            slice_value = None
            if isinstance(target.slice, ast.Constant):
                slice_value = target.slice.value
            elif isinstance(
                target.slice, ast.Index
            ) and isinstance(  # pragma: no cover - py<3.9 compat
                target.slice.value, ast.Constant
            ):
                slice_value = target.slice.value.value
            if slice_value != "improvement_notes_applied_count":
                continue
            value = getattr(node.value, "value", None)
            if value != "0":
                findings.append(
                    f'{path}:{node.lineno}: improvement_notes_applied_count must stay at "0"'
                )
    return findings


def validate(root: Path = SRC_ROOT) -> list[str]:
    findings: list[str] = []
    for path in sorted(root.rglob("*.py")) if root.is_dir() else []:
        findings.extend(_scan_trigger_tokens(path))
        findings.extend(_scan_applied_count_writes(path))
    return findings


def main() -> int:
    findings = validate()
    payload = {
        "validator": "validate_self_improvement_contract",
        "ok": not findings,
        "findings": findings,
    }
    emit_json_report(
        "validate_self_improvement_contract",
        payload,
        summary=(("scan root", SRC_ROOT), ("findings", len(findings))),
        findings=findings,
        ok_message="self-improvement metadata contract is clean.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
