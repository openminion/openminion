#!/usr/bin/env python3
"""Report direct environment reads against the EnvironmentConfig policy."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import heading, item, section, status_line  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
PATTERN = re.compile(r"os\.getenv\(|os\.environ\.get\(|os\.environ\[")
RULE_ARTIFACT_PATHS = (
    Path("scripts/baselines/env_guard_rules.json"),
    Path("openminion/scripts/baselines/env_guard_rules.json"),
)
BOUNDARY_EXCEPTION_CATEGORIES = frozenset(
    {"canonical-boundary-owner", "bootstrap", "subprocess-passthrough"}
)
WARNING_ENV_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Rule:
    path: Path
    max_calls: int
    category: str = "runtime-reader"
    reason: str = "Must use EnvironmentConfig"


def _load_rules(path: Path) -> list[Rule]:
    if not path.exists():
        print(f"[env-guard][error] Rules file not found: {path}", file=sys.stderr)
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[env-guard][error] Failed to load rules: {e}", file=sys.stderr)
        return []

    rules_raw = payload.get("rules", [])
    rules: list[Rule] = []
    for rule_data in rules_raw:
        if not isinstance(rule_data, dict):
            continue
        raw_path = str(rule_data.get("path", "")).strip()
        if not raw_path:
            continue
        max_calls = int(rule_data.get("max_calls", 0))
        category = str(rule_data.get("category", "runtime-reader")).strip()
        reason = str(rule_data.get("reason", "Must use EnvironmentConfig")).strip()
        rules.append(
            Rule(
                path=Path(raw_path),
                max_calls=max_calls,
                category=category,
                reason=reason,
            )
        )
    return rules


def _count_file(path: Path) -> tuple[int, list[int]]:
    if not path.exists():
        return 0, []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0, []
    hits = [
        lineno
        for lineno, line in enumerate(text.splitlines(), start=1)
        if PATTERN.search(line)
    ]
    return len(hits), hits


def _iter_target_files(repo_root: Path, rule: Rule) -> list[Path]:
    target = (repo_root / rule.path).resolve()
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(target.rglob("*.py"))
    return []


def _category_label(rule_category: str, *, exceeded: bool) -> str:
    norm = rule_category.strip().lower()
    if exceeded:
        return "runtime_violation"
    if norm in BOUNDARY_EXCEPTION_CATEGORIES:
        return "boundary_exception"
    return "allowance"


def _candidate_rule_paths(repo_root: Path, override: str | None) -> list[Path]:
    candidates: list[Path] = []
    if override:
        override_path = Path(override)
        candidates.extend((override_path, repo_root / override_path))
    for base_root in (repo_root.parent, repo_root, repo_root.parent.parent):
        for artifact_rel in RULE_ARTIFACT_PATHS:
            candidates.append(base_root / artifact_rel)
    return candidates


def _load_first_rules(rules_paths: list[Path]) -> list[Rule]:
    for rules_path in rules_paths:
        if not rules_path.exists():
            continue
        rules = _load_rules(rules_path)
        if rules:
            return rules
    return []


def _should_show_warnings(*, explicit_warn: bool) -> bool:
    if explicit_warn or os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return (
        str(os.environ.get("OPENMINION_ENV_GUARD_WARN", "")).strip().lower()
        in WARNING_ENV_VALUES
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Guard direct env call usage. Enforces EnvironmentConfig policy."
    )
    parser.add_argument(
        "--rules",
        default=None,
        help="Rule config path (default: checks multiple locations)",
    )
    parser.add_argument(
        "--fail-on-violation",
        action="store_true",
        help="Exit non-zero when any rule exceeds max_calls",
    )
    parser.add_argument(
        "--warn",
        action="store_true",
        help="Always print warnings; otherwise only in dev/test mode.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Only print summary, not individual violations.",
    )
    args = parser.parse_args()

    rules_paths = _candidate_rule_paths(REPO_ROOT, args.rules)
    rules = _load_first_rules(rules_paths)

    if not rules:
        print("[env-guard][error] No rules loaded. Checked:", file=sys.stderr)
        for rp in rules_paths:
            print(f"  - {rp}", file=sys.stderr)
        return 1 if args.fail_on_violation else 0

    show_warnings = _should_show_warnings(explicit_warn=bool(args.warn))

    violations: list[str] = []
    warnings: list[str] = []

    print(heading("validate/direct_env_calls.py"))
    for rule in rules:
        target_files = _iter_target_files(REPO_ROOT, rule)
        for file_path in target_files:
            count, line_numbers = _count_file(file_path)
            if count > rule.max_calls:
                rel_path = file_path.relative_to(REPO_ROOT)
                lines_str = ",".join(str(ln) for ln in line_numbers)
                label = _category_label(rule.category, exceeded=True)
                msg = (
                    f"[env-guard][violation][{label}] {rel_path}: "
                    f"{count} direct env calls (allowed {rule.max_calls}) "
                    f"-> {rel_path}:{lines_str} "
                    f"[rule_category={rule.category}]"
                )
                violations.append(msg)
            elif count > 0 and show_warnings:
                rel_path = file_path.relative_to(REPO_ROOT)
                label = _category_label(rule.category, exceeded=False)
                msg = (
                    f"[env-guard][warn][{label}] {rel_path}: "
                    f"{count} direct env calls within allowance ({rule.max_calls}) "
                    f"[rule_category={rule.category}]"
                )
                warnings.append(msg)

    if not args.summary_only:
        if warnings:
            print("")
            print(section("Warnings", kind="warn"))
            for warn in warnings:
                print(item(warn, prefix="  "))
        if violations:
            print("")
            print(section("Violations", kind="fail"))
            for viol in violations:
                print(item(viol, prefix="  "))
    print("")
    print(section("Result", kind="info"))
    print(
        item(
            f"[env-guard] checked {len(rules)} rule(s), violations={len(violations)}, warnings={len(warnings)}"
        )
    )

    if violations and args.fail_on_violation:
        print(
            status_line(
                "fail",
                "[env-guard] violations detected in strict mode",
                stream=sys.stderr,
            ),
            file=sys.stderr,
        )
        return 1

    if violations:
        print("")
        print(section("Result", kind="warn"))
        print(item("[env-guard] violations detected (warning-only mode)"))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
