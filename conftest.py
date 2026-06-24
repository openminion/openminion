"""Pytest conftest for environment guard integration.

Automatically runs environment guard checks during test collection
to catch direct os.getenv/os.environ violations early.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def pytest_configure(config):
    """Run env guard during pytest configuration."""
    # Only run in CI or when explicitly enabled
    env_guard_enabled = (
        os.environ.get("CI")
        or os.environ.get("OPENMINION_RUN_ENV_GUARD")
        or config.getoption("--env-guard", False)
    )

    if not env_guard_enabled:
        return

    # Find the guard script
    script_dir = Path(__file__).resolve().parent
    guard_script = script_dir / "scripts" / "validate" / "direct_env_calls.py"
    rules_file = script_dir / "scripts" / "baselines" / "env_guard_rules.json"

    if not guard_script.exists():
        return

    # Run guard script in warning mode
    cmd = [
        sys.executable,
        str(guard_script),
        "--rules",
        str(rules_file),
        "--warn",
        "--summary-only",
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(script_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Print output
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)

    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"[env-guard] Skipped: {e}")


def pytest_addoption(parser):
    """Add --env-guard option."""
    parser.addoption(
        "--env-guard",
        action="store_true",
        help="Run environment guard checks during test collection",
    )
