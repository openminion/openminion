#!/usr/bin/env python3
"""Ratchet pytest ``skip``/``skipif``/``xfail`` markers against a baseline.

Counts cover markers written on the test or class, in a module-level ``pytestmark``
list, or inside ``pytest.param(..., marks=...)``.

A marker counts as unconditional when it statically applies to every run: ``skip``, a
bare ``pytest.mark.<kind>``, a marker with no conditions at all (``skipif()``), and a
``skipif``/``xfail`` where any condition is a truthy literal. ``condition=`` overrides
positional arguments, otherwise every positional condition is considered and one
applying is enough. Boolean and numeric literals count directly; a string condition
counts only when it parses to a single literal, because pytest evaluates condition
strings as expressions.

Everything else is unknown and counted as conditional: names, calls, operator forms
and non-literal strings are never evaluated. The budget records which markers apply,
not whether a case is executed -- an applied ``xfail`` may still run the test
(``run=False`` prevents execution once the condition applies), and an indirect form
(a shared decorator, a ``marks=`` list assembled at runtime) is invisible to a static
scan. A file that cannot be parsed is reported as a scan error instead of being
counted as marker-free.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
import sys

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_json_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TESTS_ROOT = REPO_ROOT / "tests"
DEFAULT_BASELINE = REPO_ROOT / "scripts" / "baselines" / "test_skip_baseline.tsv"
MARKER_KINDS = frozenset({"skip", "skipif", "xfail"})
BASELINE_HEADER = "# path\ttotal\tunconditional\treason"
_TEST_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass(frozen=True)
class MarkerCounts:
    path: str
    total: int
    unconditional: int
    scan_error: str = ""


@dataclass(frozen=True)
class BaselineEntry:
    path: str
    total: int
    unconditional: int
    reason: str


def _marker_kind(node: ast.AST) -> str | None:
    """Return ``skip``/``skipif``/``xfail`` for ``pytest.mark.<kind>``."""
    target = node.func if isinstance(node, ast.Call) else node
    if not isinstance(target, ast.Attribute) or target.attr not in MARKER_KINDS:
        return None
    owner = target.value
    if not isinstance(owner, ast.Attribute) or owner.attr != "mark":
        return None
    if not isinstance(owner.value, ast.Name) or owner.value.id != "pytest":
        return None
    return target.attr


def _literal_truth(expr: ast.AST | None) -> bool | None:
    """Return the literal truth of ``expr``, or ``None`` when it is not a literal.

    pytest evaluates string conditions as expressions, so a string only establishes
    a truth value when it parses to a single constant (``"True"``, ``"0"``), never
    from the Python truthiness of the string itself.
    """
    if not isinstance(expr, ast.Constant):
        return None
    if not isinstance(expr.value, str):
        return bool(expr.value)
    try:
        parsed = ast.parse(expr.value.strip(), mode="eval")
    except SyntaxError:
        return None
    body = parsed.body
    if isinstance(body, ast.Constant) and not isinstance(body.value, str):
        return bool(body.value)
    return None


def _keyword_argument(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _marker_conditions(node: ast.Call) -> tuple[ast.AST, ...]:
    """Conditions pytest evaluates: ``condition=`` wins, else every positional one."""
    condition = _keyword_argument(node, "condition")
    if condition is not None:
        return (condition,)
    return tuple(node.args)


def _marker_is_unconditional(node: ast.AST, kind: str) -> bool:
    """Whether this marker statically applies to every run of the case."""
    if kind == "skip" or not isinstance(node, ast.Call):
        # `skip`, and a bare `pytest.mark.<kind>`, carry no conditions at all.
        return True
    conditions = _marker_conditions(node)
    if not conditions:
        return True
    return any(_literal_truth(item) is True for item in conditions)


def _pytestmark_elements(node: ast.Assign | ast.AnnAssign) -> list[ast.AST]:
    if isinstance(node, ast.Assign):
        targets: list[ast.expr] = list(node.targets)
    else:
        targets = [node.target] if node.target is not None else []
    if not any(getattr(target, "id", None) == "pytestmark" for target in targets):
        return []
    if isinstance(node.value, ast.List | ast.Tuple):
        return list(node.value.elts)
    return [node.value]


def _param_marks(node: ast.Call) -> list[ast.AST]:
    target = node.func
    if not isinstance(target, ast.Attribute) or target.attr != "param":
        return []
    if not isinstance(target.value, ast.Name) or target.value.id != "pytest":
        return []
    marks = _keyword_argument(node, "marks")
    if marks is None:
        return []
    if isinstance(marks, ast.List | ast.Tuple):
        return list(marks.elts)
    return [marks]


def count_markers(path: Path, rel: str) -> MarkerCounts:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return MarkerCounts(rel, 0, 0, f"syntax error at line {exc.lineno}")
    except (OSError, UnicodeDecodeError) as exc:
        return MarkerCounts(rel, 0, 0, f"unreadable ({type(exc).__name__})")
    total = 0
    unconditional = 0
    for node in ast.walk(tree):
        candidates: list[ast.AST] = []
        if isinstance(node, _TEST_NODES):
            candidates.extend(node.decorator_list)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            candidates.extend(_pytestmark_elements(node))
        elif isinstance(node, ast.Call):
            candidates.extend(_param_marks(node))
        for candidate in candidates:
            kind = _marker_kind(candidate)
            if kind is None:
                continue
            total += 1
            if _marker_is_unconditional(candidate, kind):
                unconditional += 1
    return MarkerCounts(rel, total, unconditional)


def iter_marker_counts(tests_root: Path) -> list[MarkerCounts]:
    counts: list[MarkerCounts] = []
    for path in sorted(tests_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        row = count_markers(path, str(path.relative_to(tests_root)))
        if row.total or row.scan_error:
            counts.append(row)
    return counts


def load_baseline(path: Path) -> dict[str, BaselineEntry]:
    if not path.is_file():
        return {}
    entries: dict[str, BaselineEntry] = {}
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip() or raw.startswith("#"):
            continue
        parts = raw.split("\t", 3)
        if len(parts) != 4:
            raise SystemExit(
                f"skip baseline line {line_number}: expected "
                "path<TAB>total<TAB>unconditional<TAB>reason"
            )
        rel, raw_total, raw_unconditional, reason = (part.strip() for part in parts)
        try:
            total = int(raw_total)
            unconditional = int(raw_unconditional)
        except ValueError as exc:
            raise SystemExit(
                f"skip baseline line {line_number}: invalid counts "
                f"{raw_total!r}/{raw_unconditional!r}"
            ) from exc
        if not reason:
            raise SystemExit(f"skip baseline line {line_number}: {rel} needs a reason")
        entries[rel] = BaselineEntry(rel, total, unconditional, reason)
    return entries


def validate(
    *, tests_root: Path, baseline_path: Path
) -> tuple[list[str], dict[str, int]]:
    rows = iter_marker_counts(tests_root)
    baseline = load_baseline(baseline_path)
    findings: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if row.scan_error:
            findings.append(f"scan_error: {row.path} ({row.scan_error})")
            continue
        entry = baseline.get(row.path)
        if entry is None:
            findings.append(
                f"unbaselined_skip_markers: {row.path} has {row.total} marker(s)"
            )
            continue
        seen.add(row.path)
        if row.total > entry.total:
            findings.append(
                f"skip_budget_grew: {row.path} has {row.total} > baseline {entry.total}"
            )
        elif row.total < entry.total:
            findings.append(
                f"stale_skip_headroom: {row.path} has {row.total} < baseline "
                f"{entry.total}; run --emit-baseline"
            )
        if row.unconditional > entry.unconditional:
            findings.append(
                f"unconditional_skip_grew: {row.path} has {row.unconditional} "
                f"unconditional > baseline {entry.unconditional}"
            )
        elif row.unconditional < entry.unconditional:
            findings.append(
                f"stale_unconditional_headroom: {row.path} has {row.unconditional} "
                f"< baseline {entry.unconditional}; run --emit-baseline"
            )
    for key, entry in sorted(baseline.items()):
        if key in seen:
            continue
        if (tests_root / entry.path).is_file():
            findings.append(
                f"stale_skip_baseline: {entry.path} has no markers; run --emit-baseline"
            )
        else:
            findings.append(f"missing_baselined_tests_file: {entry.path}")
    metrics = {
        "files_checked": len(rows),
        "markers_total": sum(row.total for row in rows),
        "unconditional_total": sum(row.unconditional for row in rows),
        "baseline_entries": len(baseline),
    }
    return findings, metrics


def emit_baseline(*, tests_root: Path, baseline_path: Path) -> tuple[bool, list[str]]:
    rows = {row.path: row for row in iter_marker_counts(tests_root)}
    baseline = load_baseline(baseline_path)
    blocked = [
        f"scan_error: {rel} ({row.scan_error})"
        for rel, row in sorted(rows.items())
        if row.scan_error
    ]
    blocked.extend(
        f"unbaselined_skip_markers: {rel} has {row.total} marker(s)"
        for rel, row in rows.items()
        if not row.scan_error and rel not in baseline
    )
    blocked.extend(
        f"skip_budget_grew: {rel} has {row.total} > baseline {baseline[rel].total}"
        for rel, row in rows.items()
        if rel in baseline and row.total > baseline[rel].total
    )
    blocked.extend(
        f"unconditional_skip_grew: {rel} has {row.unconditional} > baseline "
        f"{baseline[rel].unconditional}"
        for rel, row in rows.items()
        if rel in baseline and row.unconditional > baseline[rel].unconditional
    )
    if blocked:
        return False, blocked
    changed = set(rows) != set(baseline) or any(
        row.total < baseline[rel].total
        or row.unconditional < baseline[rel].unconditional
        for rel, row in rows.items()
    )
    if not changed:
        return False, ["unchanged_skip_baseline"]
    lines = [BASELINE_HEADER]
    for rel, row in sorted(rows.items()):
        reason = baseline[rel].reason
        lines.append(f"{rel}\t{row.total}\t{row.unconditional}\t{reason}")
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True, []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tests-root", type=Path, default=DEFAULT_TESTS_ROOT)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--emit-baseline", action="store_true")
    args = parser.parse_args(argv)
    tests_root = args.tests_root.resolve()
    if not tests_root.is_dir():
        print(f"tests/ not found at {tests_root}", file=sys.stderr)
        return 1
    if args.emit_baseline:
        written, findings = emit_baseline(
            tests_root=tests_root, baseline_path=args.baseline.resolve()
        )
        if not written:
            for finding in findings:
                print(finding, file=sys.stderr)
            return 1
        print(f"skip-marker baseline written: {args.baseline}")
        return 0
    findings, metrics = validate(
        tests_root=tests_root, baseline_path=args.baseline.resolve()
    )
    payload = {
        "validator": "test_skip_budget",
        "ok": not findings,
        "metrics": metrics,
        "findings": findings,
    }
    emit_json_report(
        "test_skip_budget",
        payload,
        summary=(
            ("files with markers", metrics["files_checked"]),
            ("markers", metrics["markers_total"]),
            ("unconditional", metrics["unconditional_total"]),
            ("baseline entries", metrics["baseline_entries"]),
        ),
        findings=findings,
        ok_message="skip-marker baseline is clean.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
