#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
PYTHON = ROOT / ".venv" / "bin" / "python3.11"

LOCAL_TESTS = (
    "tests/cli/test_default_invocation.py",
    "tests/cli/test_chat_deprecation_notice.py",
    "tests/cli/test_focus_backend_selection.py",
    "tests/e2e/tui/focus/test_local.py",
)

HELP_COMMANDS = (
    ("--help",),
    ("run", "--help"),
    ("focus", "--help"),
    ("status", "--help"),
    ("tools", "--help"),
    ("memory", "--help"),
)


def _run(command: list[str], *, env: dict[str, str]) -> int:
    return subprocess.call(command, cwd=ROOT, env=env)


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
    env["OPENMINION_LIVE_TUI_FOCUS_E2E"] = "1"
    return _run(
        [str(PYTHON), "tests/e2e/runners/run_tui_focus_e2e.py", "live"],
        env=env,
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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not PYTHON.is_file():
        print(f"python binary not found: {PYTHON}", file=sys.stderr)
        return 2
    env = os.environ.copy()
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env.setdefault("PYTHONPATH", str(ROOT / "src"))
    if args.mode in {"local", "all"}:
        result = _run_local(env)
        if result:
            return result
    if args.mode in {"live", "all"}:
        return _run_live(env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
