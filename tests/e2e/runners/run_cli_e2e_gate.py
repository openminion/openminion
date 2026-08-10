#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
PYTHON = ROOT / ".venv" / "bin" / "python3.11"
TIMEOUT_ENV = "OPENMINION_CLI_E2E_GATE_TIMEOUT_SECONDS"
DEFAULT_LIVE_TIMEOUT_SECONDS = 1800

LOCAL_TESTS = (
    "tests/cli/test_default_invocation.py",
    "tests/cli/test_focus_backend_selection.py",
    "tests/e2e/cli/focus/test_local.py",
)

HELP_COMMANDS = (
    ("--help",),
    ("run", "--help"),
    ("status", "--help"),
    ("tools", "--help"),
    ("memory", "--help"),
)


def _timeout_seconds(env: dict[str, str]) -> int:
    raw = str(env.get(TIMEOUT_ENV, "")).strip()
    if not raw:
        return DEFAULT_LIVE_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_LIVE_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_LIVE_TIMEOUT_SECONDS


def _run(
    command: list[str],
    *,
    env: dict[str, str],
    timeout_seconds: int | None = None,
) -> int:
    try:
        return subprocess.call(command, cwd=ROOT, env=env, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        rendered = " ".join(command)
        print(
            f"OpenMinion CLI E2E gate timed out after {timeout_seconds}s: {rendered}",
            file=sys.stderr,
        )
        return 124


def _run_local(env: dict[str, str]) -> int:
    for args in HELP_COMMANDS:
        result = _run(
            [str(PYTHON), "-m", "openminion", *args],
            env=env,
        )
        if result:
            return result
    return _run(
        [str(PYTHON), "-m", "pytest", "-q", *LOCAL_TESTS, "-ra"],
        env=env,
    )


def _run_live(env: dict[str, str]) -> int:
    env["OPENMINION_LIVE_CLI_FOCUS_E2E"] = "1"
    return _run(
        [str(PYTHON), "tests/e2e/runners/run_cli_focus_e2e.py", "live"],
        env=env,
        timeout_seconds=_timeout_seconds(env),
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the canonical OpenMinion CLI and Focus E2E gate."
    )
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("local", "live", "all"),
        default="local",
        help="Local contracts, live Focus smoke, or both.",
    )
    return parser.parse_args(argv)


def _set_runtime_roots(env: dict[str, str]) -> None:
    home_root = ROOT.resolve()
    data_root = home_root / ".openminion"
    env["OPENMINION_HOME"] = str(home_root)
    env["OPENMINION_DATA_ROOT"] = str(data_root)
    env["OPENMINION_GENERATED_ROOT"] = str(data_root / "runtime")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not PYTHON.is_file():
        print(f"python binary not found: {PYTHON}", file=sys.stderr)
        return 2
    env = os.environ.copy()
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env.setdefault("PYTHONPATH", str(ROOT / "src"))
    _set_runtime_roots(env)
    if args.mode in {"local", "all"}:
        result = _run_local(env)
        if result:
            return result
    if args.mode in {"live", "all"}:
        return _run_live(env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
