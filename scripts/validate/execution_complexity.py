#!/usr/bin/env python3
"""Enforce monotonic complexity limits for the agent execution package."""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_json_report  # noqa: E402
from scripts.validate import helper_duplicates, max_file_loc, method_loc, passthrough  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
EXECUTION_ROOT = REPO_ROOT / "src/openminion/services/agent/execution"
BASELINE_PATH = REPO_ROOT / "scripts/baselines/agent_execution_complexity.tsv"
FILE_LOC_LIMIT = 500
METHOD_LOC_LIMIT = 100
INTERNAL_PARAMETER_LIMIT = 10
DISPATCH_METHODS = {"run_required_tool_lane", "handle_unforced_tool_calls"}


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    detail: str


def load_baseline(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip() or raw.startswith("#"):
            continue
        parts = raw.split("\t", 1)
        if len(parts) != 2:
            raise SystemExit(
                f"agent-execution baseline line {line_number}: expected key<TAB>value"
            )
        key, raw_value = (part.strip() for part in parts)
        try:
            values[key] = int(raw_value)
        except ValueError as exc:
            raise SystemExit(
                f"agent-execution baseline line {line_number}: invalid value {raw_value!r}"
            ) from exc
    required = {"package_loc", "passthrough_count", "dispatch_owner_count"}
    missing = required - values.keys()
    if missing:
        raise SystemExit(
            "agent-execution baseline missing keys: " + ", ".join(sorted(missing))
        )
    return values


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def _parameter_count(
    node: ast.FunctionDef | ast.AsyncFunctionDef, *, method: bool
) -> int:
    parameters = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    count = (
        len(parameters)
        + int(node.args.vararg is not None)
        + int(node.args.kwarg is not None)
    )
    if method and parameters and parameters[0].arg in {"self", "cls"}:
        count -= 1
    return count


class _StructureVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.class_depth = 0
        self.function_depth = 0
        self.parameter_findings: list[Finding] = []
        self.dispatch_owners: list[tuple[str, str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        methods = {
            child.name
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if DISPATCH_METHODS <= methods:
            self.dispatch_owners.append((self.path, node.name))
        self.class_depth += 1
        self.generic_visit(node)
        self.class_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        method = self.class_depth > 0 and self.function_depth == 0
        count = _parameter_count(node, method=method)
        if node.name.startswith("_") and not node.name.startswith("__"):
            if count > INTERNAL_PARAMETER_LIMIT:
                self.parameter_findings.append(
                    Finding(
                        "internal_parameter_limit",
                        self.path,
                        f"{node.name} has {count} parameters > {INTERNAL_PARAMETER_LIMIT}",
                    )
                )
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1


def _structure_findings(
    files: list[Path], *, repo_root: Path
) -> tuple[list[Finding], list[tuple[str, str]]]:
    findings: list[Finding] = []
    dispatch_owners: list[tuple[str, str]] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _StructureVisitor(_relative(path, repo_root))
        visitor.visit(tree)
        findings.extend(visitor.parameter_findings)
        dispatch_owners.extend(visitor.dispatch_owners)
    return findings, dispatch_owners


def validate(
    *, repo_root: Path, execution_root: Path, baseline_path: Path
) -> tuple[list[Finding], dict[str, int]]:
    baseline = load_baseline(baseline_path)
    files = max_file_loc.source_files(execution_root)
    file_locs = {path: max_file_loc.count_loc(path) for path in files}
    package_loc = sum(file_locs.values())
    findings: list[Finding] = []
    if package_loc > baseline["package_loc"]:
        findings.append(
            Finding(
                "package_loc_growth",
                _relative(execution_root, repo_root),
                f"{package_loc} LOC exceeds baseline {baseline['package_loc']}",
            )
        )
    for path, loc in file_locs.items():
        if loc > FILE_LOC_LIMIT:
            findings.append(
                Finding(
                    "file_loc_limit",
                    _relative(path, repo_root),
                    f"{loc} LOC exceeds {FILE_LOC_LIMIT}",
                )
            )
    methods = method_loc.iter_methods(repo_root=repo_root, source_root=execution_root)
    for method in methods:
        if method.loc > METHOD_LOC_LIMIT:
            findings.append(
                Finding(
                    "method_loc_limit",
                    method.path,
                    f"{method.qualname} has {method.loc} LOC > {METHOD_LOC_LIMIT}",
                )
            )
    structure_findings, dispatch_owners = _structure_findings(
        files, repo_root=repo_root
    )
    findings.extend(structure_findings)
    canonical_dispatch = any(
        path.endswith("services/agent/execution/executor.py") and name == "TurnExecutor"
        for path, name in dispatch_owners
    )
    if not canonical_dispatch:
        findings.append(
            Finding(
                "canonical_dispatch_missing",
                _relative(execution_root, repo_root),
                "TurnExecutor must remain the combined required/unforced dispatcher",
            )
        )
    if len(dispatch_owners) > baseline["dispatch_owner_count"]:
        rendered = ", ".join(f"{path}:{name}" for path, name in dispatch_owners)
        findings.append(
            Finding(
                "dispatch_owner_growth",
                _relative(execution_root, repo_root),
                f"{len(dispatch_owners)} owners exceed baseline "
                f"{baseline['dispatch_owner_count']}: {rendered}",
            )
        )
    pass_throughs = passthrough.scan(execution_root)
    if len(pass_throughs) > baseline["passthrough_count"]:
        findings.append(
            Finding(
                "passthrough_growth",
                _relative(execution_root, repo_root),
                f"{len(pass_throughs)} wrappers exceed baseline "
                f"{baseline['passthrough_count']}",
            )
        )
    duplicate_hits = helper_duplicates.scan(execution_root, repo_root=repo_root)
    findings.extend(
        Finding("duplicate_helpers", _relative(execution_root, repo_root), hit)
        for hit in duplicate_hits
    )
    metrics = {
        "files": len(files),
        "package_loc": package_loc,
        "methods": len(methods),
        "dispatch_owners": len(dispatch_owners),
        "passthroughs": len(pass_throughs),
        "duplicate_helpers": len(duplicate_hits),
    }
    return findings, metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--execution-root", type=Path, default=EXECUTION_ROOT)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    args = parser.parse_args(argv)
    findings, metrics = validate(
        repo_root=args.repo_root.resolve(),
        execution_root=args.execution_root.resolve(),
        baseline_path=args.baseline.resolve(),
    )
    payload = {
        "validator": "agent_execution_complexity",
        "ok": not findings,
        "metrics": metrics,
        "findings": [asdict(finding) for finding in findings],
    }
    emit_json_report(
        "agent_execution_complexity",
        payload,
        summary=tuple(metrics.items()),
        findings=[
            f"{finding.code}: {finding.path} - {finding.detail}" for finding in findings
        ],
        ok_message="agent execution complexity ratchets are clean.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
