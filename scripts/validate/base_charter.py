#!/usr/bin/env python3
"""Validate the layout and dependency direction of `openminion.base`."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from typing import NamedTuple

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_json_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_ROOT = REPO_ROOT / "src" / "openminion" / "base"
COMPLEXITY_BASELINE = (
    REPO_ROOT / "scripts" / "baselines" / "base_foundation_ratchet.tsv"
)
BASE_LOC_LIMIT = 9_500
DEFAULT_FILE_LOC_LIMIT = 500
MAX_CALLABLE_LOC = 100
APPROVED_LARGE_FILES = {
    "config/mcp.py": (719, "declarative MCP catalog retained by the MCPH owner"),
}
ALLOWED_ROOT_FILES = {
    "README.md",
    "__init__.py",
    "constants.py",
    "debug.py",
    "generated_paths.py",
    "logging.py",
    "protocol.py",
    "redaction.py",
    "time.py",
    "types.py",
    "user_io.py",
    "version.py",
}
ALLOWED_TOP_LEVEL_DIRS = {"channel", "config", "errors", "runtime"}
FORBIDDEN_UPWARD_AREAS = {"api", "cli", "modules", "services", "tools"}


class UpwardImport(NamedTuple):
    path: str
    line: int
    target: str


class ComplexityBudget(NamedTuple):
    loc: int
    callables: int
    max_callable_loc: int
    max_parameters: int


def _callable_size(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    return (node.end_lineno or node.lineno) - node.lineno + 1


def _parameter_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    args = node.args
    return (
        len(args.posonlyargs)
        + len(args.args)
        + len(args.kwonlyargs)
        + int(args.vararg is not None)
        + int(args.kwarg is not None)
    )


def measure_complexity(
    root: Path = BASE_ROOT,
) -> tuple[dict[str, ComplexityBudget], list[str]]:
    measured: dict[str, ComplexityBudget] = {}
    errors: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError) as exc:
            errors.append(f"Cannot measure {rel}: {exc}")
            continue
        callables = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        measured[rel] = ComplexityBudget(
            loc=len(source.splitlines()),
            callables=len(callables),
            max_callable_loc=max(map(_callable_size, callables), default=0),
            max_parameters=max(map(_parameter_count, callables), default=0),
        )
    return measured, errors


def load_complexity_baseline(
    path: Path = COMPLEXITY_BASELINE,
) -> dict[str, ComplexityBudget]:
    rows = path.read_text(encoding="utf-8").splitlines()
    expected_header = "path\tloc\tcallables\tmax_callable_loc\tmax_parameters"
    if not rows or rows[0] != expected_header:
        raise ValueError(f"Invalid Base complexity baseline header: {path}")
    baseline: dict[str, ComplexityBudget] = {}
    for line_number, row in enumerate(rows[1:], start=2):
        fields = row.split("\t")
        if len(fields) != 5 or fields[0] in baseline:
            raise ValueError(
                f"Invalid Base complexity baseline row {line_number}: {row}"
            )
        baseline[fields[0]] = ComplexityBudget(*(int(value) for value in fields[1:]))
    return baseline


def validate_complexity_ratchets(
    root: Path = BASE_ROOT,
    *,
    baseline: dict[str, ComplexityBudget] | None = None,
    total_loc_limit: int = BASE_LOC_LIMIT,
) -> list[str]:
    if baseline is None:
        try:
            baseline = load_complexity_baseline()
        except (OSError, ValueError) as exc:
            return [str(exc)]
    current, errors = measure_complexity(root)
    current_paths = set(current)
    baseline_paths = set(baseline)
    for path in sorted(current_paths - baseline_paths):
        errors.append(
            f"Unreviewed Base Python owner: {path}; apply the local -> area -> Base owner ladder"
        )
    for path in sorted(baseline_paths - current_paths):
        errors.append(
            f"Stale Base ratchet path: {path}; remove the retired baseline row"
        )

    metric_names = ComplexityBudget._fields
    for path in sorted(current_paths & baseline_paths):
        actual = current[path]
        budget = baseline[path]
        file_limit = APPROVED_LARGE_FILES.get(path, (DEFAULT_FILE_LOC_LIMIT, ""))[0]
        if actual.loc > file_limit:
            errors.append(
                f"Base file LOC ceiling exceeded: {path}: {actual.loc} > {file_limit}"
            )
        if actual.max_callable_loc > MAX_CALLABLE_LOC:
            errors.append(
                f"Base callable LOC ceiling exceeded: {path}: "
                f"{actual.max_callable_loc} > {MAX_CALLABLE_LOC}"
            )
        for metric in metric_names:
            actual_value = getattr(actual, metric)
            budget_value = getattr(budget, metric)
            if actual_value > budget_value:
                errors.append(
                    f"Base {metric} ratchet increased: {path}: "
                    f"{actual_value} > {budget_value}"
                )
            elif actual_value < budget_value:
                errors.append(
                    f"Stale Base {metric} ratchet: {path}: "
                    f"lower baseline from {budget_value} to {actual_value}"
                )
    total_loc = sum(item.loc for item in current.values())
    if total_loc > total_loc_limit:
        errors.append(
            f"Base area LOC ceiling exceeded: {total_loc} > {total_loc_limit}"
        )
    return errors


def _resolve_import_target(node: ast.ImportFrom, package: str) -> str:
    if not node.level:
        return node.module or ""
    relative_name = "." * node.level + (node.module or "")
    try:
        return importlib.util.resolve_name(relative_name, package)
    except (ImportError, ValueError):
        return node.module or ""


def _is_upward_target(target: str) -> bool:
    return any(
        target == f"openminion.{area}" or target.startswith(f"openminion.{area}.")
        for area in FORBIDDEN_UPWARD_AREAS
    )


def find_upward_imports(root: Path = BASE_ROOT) -> tuple[list[UpwardImport], list[str]]:
    imports: list[UpwardImport] = []
    errors: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        package_parts = ["openminion", "base", *path.relative_to(root).parent.parts]
        package = ".".join(package_parts)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            errors.append(f"Cannot inspect {rel}: {exc}")
            continue
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                target = _resolve_import_target(node, package)
                targets.append(target)
                if target == "openminion":
                    targets.extend(f"openminion.{alias.name}" for alias in node.names)
            imports.extend(
                UpwardImport(rel, node.lineno, target)
                for target in targets
                if _is_upward_target(target)
            )
    return sorted(imports), errors


def validate_upward_imports(
    root: Path = BASE_ROOT,
    *,
    baseline: dict[UpwardImport, str] | None = None,
) -> list[str]:
    allowed = {} if baseline is None else baseline
    current, errors = find_upward_imports(root)
    current_set = set(current)
    allowed_set = set(allowed)
    for edge in sorted(current_set - allowed_set):
        errors.append(
            f"New upward import from base: {edge.path}:{edge.line}: {edge.target}"
        )
    for edge in sorted(allowed_set - current_set):
        errors.append(
            "Stale base upward-import baseline entry: "
            f"{edge.path}:{edge.line}: {edge.target} ({allowed[edge]})"
        )
    return errors


def validate_root_layout(root: Path = BASE_ROOT) -> list[str]:
    errors: list[str] = []
    root_files = sorted(path.name for path in root.iterdir() if path.is_file())
    unexpected_root_files = [
        name for name in root_files if name not in ALLOWED_ROOT_FILES
    ]
    if unexpected_root_files:
        errors.append(
            "Unexpected root files under base/: " + ", ".join(unexpected_root_files)
        )

    top_level_dirs = sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    )
    unexpected_dirs = [
        name for name in top_level_dirs if name not in ALLOWED_TOP_LEVEL_DIRS
    ]
    if unexpected_dirs:
        errors.append("Unexpected top-level base dirs: " + ", ".join(unexpected_dirs))

    missing_dirs = sorted(ALLOWED_TOP_LEVEL_DIRS.difference(top_level_dirs))
    if missing_dirs:
        errors.append("Missing admitted base subpackages: " + ", ".join(missing_dirs))

    if not (root / "README.md").exists():
        errors.append("src/openminion/base/README.md missing")
    return errors


def main() -> int:
    metrics, metric_errors = measure_complexity()
    errors = [
        *validate_root_layout(),
        *validate_upward_imports(),
        *validate_complexity_ratchets(),
    ]
    total_loc = sum(item.loc for item in metrics.values())
    result = {
        "ok": not errors,
        "allowed_root_files": sorted(ALLOWED_ROOT_FILES),
        "admitted_subpackages": sorted(ALLOWED_TOP_LEVEL_DIRS),
        "upward_imports": 0,
        "base_python_files": len(metrics),
        "base_loc": total_loc,
        "base_loc_limit": BASE_LOC_LIMIT,
        "max_callable_loc": max(
            (item.max_callable_loc for item in metrics.values()), default=0
        ),
        "metric_errors": metric_errors,
    }
    emit_json_report(
        "validate/base_charter.py",
        result,
        summary=(
            ("base root", BASE_ROOT),
            ("allowed root files", len(ALLOWED_ROOT_FILES)),
            ("admitted subpackages", len(ALLOWED_TOP_LEVEL_DIRS)),
            ("allowed upward imports", 0),
            ("reviewed Python owners", len(metrics)),
            ("base LOC", f"{total_loc}/{BASE_LOC_LIMIT}"),
        ),
        findings=errors,
        ok_message="base layout, dependency direction, and monotonic budgets match the charter.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
