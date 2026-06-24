#!/usr/bin/env python3
"""Run pytest for selector sets emitted by CI change detection."""

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run pytest for JSON-defined selectors."
    )
    parser.add_argument(
        "--selectors-json", required=True, help="JSON array of pytest selectors/paths."
    )
    parser.add_argument(
        "--modules-json",
        default="[]",
        help="JSON array of module names for coverage targets.",
    )
    parser.add_argument(
        "--junitxml",
        default=str(CI_ARTIFACTS_ROOT / "test-results" / "junit.xml"),
    )
    parser.add_argument(
        "--coverage-xml",
        default=str(CI_ARTIFACTS_ROOT / "test-results" / "coverage.xml"),
    )
    parser.add_argument("--with-coverage", action="store_true")
    parser.add_argument("--extra-arg", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    selectors = load_json_list(args.selectors_json)
    modules = load_json_list(args.modules_json)

    if not selectors:
        print("No pytest selectors provided; skipping test run.")
        return 0

    junit_path = Path(args.junitxml)
    junit_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, "-m", "pytest", "-q", *selectors, f"--junitxml={junit_path}"]

    if args.with_coverage:
        coverage_xml = Path(args.coverage_xml)
        coverage_xml.parent.mkdir(parents=True, exist_ok=True)
        coverage_targets = sorted({module.replace("-", "_") for module in modules})
        if not coverage_targets:
            coverage_targets = ["openminion"]
        for target in coverage_targets:
            cmd.extend(["--cov", target])
        cmd.append(f"--cov-report=xml:{coverage_xml}")

    for item in args.extra_arg:
        cmd.append(str(item))

    print("Running:", " ".join(cmd))
    env = build_ci_runtime_env(ROOT)
    proc = subprocess.run(cmd, env=env)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
