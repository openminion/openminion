#!/usr/bin/env python3
"""Run migration-focused checks for selected modules."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common.ci_support import build_ci_runtime_env, load_json_list  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CI_ARTIFACTS_ROOT = ROOT / ".openminion" / "runtime" / "ci"

EXPLICIT_SELECTORS = {
    "openminion-storage": [
        "openminion/tests/storage/test_migrations_runner.py",
        "openminion/tests/storage/test_migrations_registry.py",
        "openminion/tests/storage/test_migrations_policy.py",
    ]
}


def _discover_module_selectors(module: str) -> list[str]:
    if module in EXPLICIT_SELECTORS:
        return list(EXPLICIT_SELECTORS[module])

    test_root = ROOT / module / "tests"
    if not test_root.exists():
        return []

    selectors: list[str] = []
    patterns = ["test_migration*.py", "test_*migrat*.py", "test_*schema*.py"]
    for pattern in patterns:
        for path in sorted(test_root.glob(pattern)):
            selectors.append(str(path.relative_to(ROOT)))

    deduped: list[str] = []
    seen: set[str] = set()
    for selector in selectors:
        if selector in seen:
            continue
        seen.add(selector)
        deduped.append(selector)
    return deduped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run migration checks for modules.")
    parser.add_argument(
        "--modules-json", required=True, help="JSON list of module names"
    )
    parser.add_argument(
        "--junitxml",
        default=str(CI_ARTIFACTS_ROOT / "migrations" / "junit.xml"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    modules = load_json_list(args.modules_json)

    selectors: list[str] = []
    for module in modules:
        selectors.extend(_discover_module_selectors(module))

    if not selectors:
        print(
            "No migration selectors found for selected modules; skipping migration checks."
        )
        return 0

    junit_path = Path(args.junitxml)
    junit_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, "-m", "pytest", "-q", *selectors, f"--junitxml={junit_path}"]
    print("Running:", " ".join(cmd))
    env = build_ci_runtime_env(ROOT)
    proc = subprocess.run(cmd, env=env)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
