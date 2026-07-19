"""Validate the public root layout of the `openminion.api` package."""

from __future__ import annotations

import ast
import csv
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_json_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "src" / "openminion" / "api"
BASELINE_ROOT = REPO_ROOT / "scripts" / "baselines"
COMPLEXITY_BASELINE = BASELINE_ROOT / "api_complexity.tsv"
ROUTE_IMPORT_BASELINE = BASELINE_ROOT / "api_route_owner_imports.tsv"
ALLOWED_ROOT_FILES = {
    "README.md",
    "__init__.py",
    "agent.py",
    "config.py",
    "constants.py",
    "handoff.py",
    "metrics.py",
    "metrics_registry.py",
    "runtime.py",
    "turns.py",
}
REQUIRED_SUBPACKAGES = {
    "core",
    "operations",
    "queries",
    "responses",
    "routes",
    "server",
}


def validate_layout(api_root: Path) -> tuple[list[str], list[str], list[str]]:
    root_files = sorted(path.name for path in api_root.iterdir() if path.is_file())
    disallowed = sorted(name for name in root_files if name not in ALLOWED_ROOT_FILES)
    subpackages = {
        path.name
        for path in api_root.iterdir()
        if path.is_dir() and (path / "__init__.py").exists()
    }
    return root_files, disallowed, sorted(REQUIRED_SUBPACKAGES - subpackages)


def collect_route_owner_imports(api_root: Path) -> set[tuple[str, str]]:
    imports: set[tuple[str, str]] = set()
    for path in sorted((api_root / "routes").glob("*.py")):
        source = str(path.relative_to(api_root))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            imports.update(
                (source, name)
                for name in names
                if name.startswith(("openminion.modules.", "openminion.services."))
            )
    return imports


def collect_complexity(api_root: Path) -> dict[str, int]:
    max_callable = 0
    for path in api_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        max_callable = max(
            [
                node.end_lineno - node.lineno + 1
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            + [max_callable]
        )
    return {
        "max_callable_loc": max_callable,
        "max_route_loc": max(
            len(path.read_text(encoding="utf-8").splitlines())
            for path in (api_root / "routes").glob("*.py")
        ),
        "queries/runtime_reports.py": _line_count(
            api_root / "queries" / "runtime_reports.py"
        ),
        "runtime.py": _line_count(api_root / "runtime.py"),
        "server/app.py": _line_count(api_root / "server" / "app.py"),
    }


def compare_ratchet(current: dict[str, int], baseline: dict[str, int]) -> list[str]:
    return [
        f"API complexity increased: {name} {baseline[name]} -> {current[name]}"
        for name in sorted(baseline)
        if current.get(name, 0) > baseline[name]
    ]


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _read_pairs(path: Path) -> set[tuple[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = csv.reader(handle, delimiter="\t")
        next(rows, None)
        return {(source, imported) for source, imported in rows}


def _read_metrics(path: Path) -> dict[str, int]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = csv.reader(handle, delimiter="\t")
        next(rows, None)
        return {name: int(value) for name, value in rows}


def main() -> int:
    all_root_files, disallowed, missing_packages = validate_layout(API_ROOT)
    route_imports = collect_route_owner_imports(API_ROOT)
    route_baseline = _read_pairs(ROUTE_IMPORT_BASELINE)
    new_route_imports = sorted(route_imports - route_baseline)
    stale_route_imports = sorted(route_baseline - route_imports)
    complexity = collect_complexity(API_ROOT)
    complexity_baseline = _read_metrics(COMPLEXITY_BASELINE)
    complexity_findings = compare_ratchet(complexity, complexity_baseline)
    result = {
        "ok": not any(
            (
                disallowed,
                missing_packages,
                new_route_imports,
                stale_route_imports,
                complexity_findings,
            )
        ),
        "allowed_root_files": sorted(ALLOWED_ROOT_FILES),
        "root_files": all_root_files,
        "disallowed_root_files": disallowed,
        "required_subpackages": sorted(REQUIRED_SUBPACKAGES),
        "missing_subpackages": missing_packages,
        "route_owner_imports": sorted(route_imports),
        "new_route_owner_imports": new_route_imports,
        "stale_route_owner_imports": stale_route_imports,
        "complexity": complexity,
        "complexity_baseline": complexity_baseline,
    }
    findings = []
    if disallowed:
        findings.append(f"Unexpected root files under api/: {', '.join(disallowed)}")
    if missing_packages:
        findings.append(
            f"Missing required api subpackages: {', '.join(missing_packages)}"
        )
    findings.extend(
        f"New route-owned domain import: {source} -> {imported}"
        for source, imported in new_route_imports
    )
    findings.extend(
        f"Remove stale route import baseline: {source} -> {imported}"
        for source, imported in stale_route_imports
    )
    findings.extend(complexity_findings)
    emit_json_report(
        "validate/api_layout.py",
        result,
        summary=(
            ("api root", API_ROOT),
            ("allowed root files", len(ALLOWED_ROOT_FILES)),
            ("required subpackages", len(REQUIRED_SUBPACKAGES)),
            ("route owner imports", len(route_imports)),
            ("max callable LOC", complexity["max_callable_loc"]),
        ),
        findings=findings,
        ok_message="api layout, route-owner imports, and complexity match exact ratchets.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
