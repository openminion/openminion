#!/usr/bin/env python3
"""Detect changed/impacted OpenMinion modules for CI workflows.

This script keeps CI module selection config-driven so adding a new module can
be mostly a catalog update instead of workflow YAML edits.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common.repo_modules import discover_repo_modules, load_pyproject_document  # noqa: E402


ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = ROOT / "ci" / "module_catalog.json"
GITHUB_OUTPUT_KEYS = (
    "base_sha",
    "head_sha",
    "shared_change",
    "changed_files",
    "all_modules",
    "changed_modules",
    "impacted_modules",
    "core_modules",
    "always_test_modules",
    "test_modules",
    "test_paths",
    "all_test_paths",
    "migration_modules",
    "integration_modules",
    "integration_scenarios",
    "integration_selectors",
    "test_module_matrix",
    "migration_module_matrix",
    "integration_scenario_matrix",
)


@dataclass(frozen=True)
class DiffContext:
    base_sha: str
    head_sha: str
    changed_files: list[str]


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def load_catalog() -> dict[str, Any]:
    if not CATALOG_PATH.exists():
        raise FileNotFoundError(f"Missing catalog file: {CATALOG_PATH}")
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _looks_like_sha(value: str) -> bool:
    value = value.strip()
    if not value:
        return False
    return bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", value))


def _is_null_sha(value: str) -> bool:
    value = value.strip()
    return bool(value) and set(value) == {"0"}


def collect_changed_files(base_sha: str, head_sha: str) -> list[str]:
    attempts: list[list[str]] = []

    if base_sha and _looks_like_sha(base_sha) and not _is_null_sha(base_sha):
        if head_sha and _looks_like_sha(head_sha):
            attempts.append(["git", "diff", "--name-only", f"{base_sha}...{head_sha}"])
            attempts.append(["git", "diff", "--name-only", base_sha, head_sha])
        else:
            attempts.append(["git", "diff", "--name-only", f"{base_sha}...HEAD"])
            attempts.append(["git", "diff", "--name-only", base_sha, "HEAD"])

    if head_sha and _looks_like_sha(head_sha):
        attempts.append(["git", "diff", "--name-only", f"{head_sha}~1", head_sha])

    attempts.append(["git", "diff", "--name-only", "HEAD~1", "HEAD"])

    for cmd in attempts:
        proc = _run(cmd, check=False)
        if proc.returncode != 0:
            continue
        files = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        if files:
            return sorted(set(files))

    return []


def parse_requirement_name(requirement: str) -> str:
    token = re.split(r"[<>=!~;\[\]\s]", requirement, maxsplit=1)[0].strip()
    return token.lower().replace("_", "-")


def build_reverse_dependency_graph(modules: list[str]) -> dict[str, set[str]]:
    module_set = set(modules)
    reverse: dict[str, set[str]] = defaultdict(set)

    for module in modules:
        pyproject = ROOT / module / "pyproject.toml"
        doc = load_pyproject_document(pyproject)
        if not doc:
            continue
        deps = doc.get("project", {}).get("dependencies", [])
        if not isinstance(deps, list):
            continue
        for dep in deps:
            dep_name = parse_requirement_name(str(dep))
            if dep_name in module_set:
                reverse[dep_name].add(module)

    for module in modules:
        reverse.setdefault(module, set())

    return reverse


def downstream_closure(
    changed: set[str], reverse_graph: dict[str, set[str]]
) -> set[str]:
    impacted = set(changed)
    queue: deque[str] = deque(sorted(changed))
    while queue:
        node = queue.popleft()
        for child in sorted(reverse_graph.get(node, set())):
            if child in impacted:
                continue
            impacted.add(child)
            queue.append(child)
    return impacted


def has_shared_change(changed_files: list[str], catalog: dict[str, Any]) -> bool:
    prefixes = [str(x) for x in catalog.get("shared_change_prefixes", [])]
    exact = {str(x) for x in catalog.get("shared_change_files", [])}
    for path in changed_files:
        if path in exact:
            return True
        if any(path.startswith(prefix) for prefix in prefixes):
            return True
    return False


def module_from_path(path: str, modules: list[str]) -> str | None:
    for module in modules:
        if path == module or path.startswith(f"{module}/"):
            return module
    return None


def to_test_paths(modules: list[str]) -> list[str]:
    return [
        f"{module}/tests" for module in modules if (ROOT / module / "tests").exists()
    ]


def _catalog_module_set(
    catalog: dict[str, Any], key: str, modules: set[str]
) -> set[str]:
    return {str(name) for name in catalog.get(key, []) if str(name) in modules}


def build_scenarios(
    catalog: dict[str, Any], selected_modules: set[str]
) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    for item in catalog.get("integration_scenarios", []):
        module = str(item.get("module", "")).strip()
        selectors = [str(s) for s in item.get("selectors", [])]
        if module not in selected_modules or not selectors:
            continue
        scenarios.append(
            {
                "id": str(item.get("id", "")).strip(),
                "module": module,
                "description": str(item.get("description", "")).strip(),
                "selectors": selectors,
            }
        )
    return scenarios


def list_to_matrix(items: list[str], *, key: str = "module") -> dict[str, Any]:
    return {"include": [{key: value} for value in items]}


def selectors_from_scenarios(scenarios: list[dict[str, Any]]) -> list[str]:
    return list(
        dict.fromkeys(
            str(selector)
            for scenario in scenarios
            for selector in scenario.get("selectors", [])
        )
    )


def build_result(
    catalog: dict[str, Any], modules: list[str], diff: DiffContext
) -> dict[str, Any]:
    all_module_set = set(modules)
    changed_modules = {
        module
        for path in diff.changed_files
        for module in [module_from_path(path, modules)]
        if module is not None
    }

    reverse_graph = build_reverse_dependency_graph(modules)
    impacted_modules = downstream_closure(changed_modules, reverse_graph)

    shared_change = has_shared_change(diff.changed_files, catalog)
    core_modules = _catalog_module_set(catalog, "core_modules", all_module_set)
    always_test_modules = _catalog_module_set(
        catalog, "always_test_modules", all_module_set
    )
    migration_modules_cfg = _catalog_module_set(
        catalog, "migration_modules", all_module_set
    )
    integration_modules_cfg = _catalog_module_set(
        catalog, "integration_modules", all_module_set
    )

    if shared_change:
        impacted_modules = set(modules)

    if not changed_modules and diff.changed_files and not shared_change:
        impacted_modules.update(core_modules)

    if not diff.changed_files:
        # Fallback for manually-triggered workflows without a useful diff.
        impacted_modules.update(core_modules)

    test_modules = impacted_modules | core_modules | always_test_modules

    selected_migration_modules = sorted(migration_modules_cfg & impacted_modules)
    selected_integration_modules = sorted(integration_modules_cfg)

    scenarios = build_scenarios(catalog, set(selected_integration_modules))
    integration_selectors = selectors_from_scenarios(scenarios)

    test_modules_sorted = sorted(test_modules)
    test_paths = to_test_paths(test_modules_sorted)
    all_test_paths = to_test_paths(modules)

    result: dict[str, Any] = {
        "base_sha": diff.base_sha,
        "head_sha": diff.head_sha,
        "changed_files": diff.changed_files,
        "shared_change": shared_change,
        "all_modules": modules,
        "changed_modules": sorted(changed_modules),
        "impacted_modules": sorted(impacted_modules),
        "core_modules": sorted(core_modules),
        "always_test_modules": sorted(always_test_modules),
        "test_modules": test_modules_sorted,
        "test_paths": test_paths,
        "all_test_paths": all_test_paths,
        "migration_modules": selected_migration_modules,
        "integration_modules": selected_integration_modules,
        "integration_scenarios": scenarios,
        "integration_selectors": integration_selectors,
        "test_module_matrix": list_to_matrix(test_modules_sorted),
        "migration_module_matrix": list_to_matrix(selected_migration_modules),
        "integration_scenario_matrix": {
            "include": [
                {
                    "id": s["id"],
                    "module": s["module"],
                    "description": s["description"],
                    "selectors": s["selectors"],
                }
                for s in scenarios
            ]
        },
    }

    return result


def _write_gha_output(path: Path, result: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for key in GITHUB_OUTPUT_KEYS:
            value = result.get(key)
            if key == "shared_change":
                fh.write(f"{key}={str(value).lower()}\n")
            elif isinstance(value, str):
                fh.write(f"{key}={value}\n")
            else:
                fh.write(f"{key}={json.dumps(value, separators=(',', ':'))}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect changed OpenMinion modules for CI workflows."
    )
    parser.add_argument("--base-sha", default=os.environ.get("CI_BASE_SHA", ""))
    parser.add_argument("--head-sha", default=os.environ.get("CI_HEAD_SHA", ""))
    parser.add_argument("--write-github-output", default="")
    parser.add_argument(
        "--print-field", default="", help="Print only one top-level result field."
    )
    parser.add_argument(
        "--pretty", action="store_true", help="Pretty-print JSON output."
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_sha = str(args.base_sha or "").strip()
    head_sha = str(args.head_sha or "").strip()

    catalog = load_catalog()
    modules = discover_repo_modules(ROOT)

    diff = DiffContext(
        base_sha=base_sha,
        head_sha=head_sha,
        changed_files=collect_changed_files(base_sha, head_sha),
    )
    result = build_result(catalog, modules, diff)

    if args.write_github_output:
        _write_gha_output(Path(args.write_github_output), result)

    if args.print_field:
        value = result.get(args.print_field)
        if isinstance(value, str):
            sys.stdout.write(value)
        else:
            sys.stdout.write(
                json.dumps(
                    value, indent=2 if args.pretty else None, sort_keys=args.pretty
                )
            )
        sys.stdout.write("\n")
        return 0

    if args.pretty:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
