#!/usr/bin/env python3
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

TEST_FILES = (
    "tests/tools/exec/test_command_parser.py",
    "tests/tools/exec/test_plugin.py",
    "tests/tools/exec/test_process_shell_family.py",
    "tests/brain/test_runner.py",
    "tests/brain/test_runner_tools.py",
)


def _with_src_path(env: dict[str, str]) -> dict[str, str]:
    updated = dict(env)
    current = updated.get("PYTHONPATH", "").strip()
    updated["PYTHONPATH"] = "src" if not current else f"src{os.pathsep}{current}"
    return updated


def main() -> int:
    repo_root = Path(__file__).resolve().parents[3]
    cmd = [sys.executable, "-m", "pytest", "-q", *TEST_FILES]
    env = _with_src_path(os.environ)
    print(
        "[exec-validation-matrix] lane start",
        {
            "platform": platform.platform(),
            "python": sys.executable,
            "cwd": str(repo_root),
        },
    )
    completed = subprocess.run(cmd, cwd=repo_root, env=env, check=False)
    return int(completed.returncode)
