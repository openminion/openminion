from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_run_cli_chat_probe_script_imports_without_brain_cycle() -> None:
    root = Path(__file__).resolve().parents[3]
    script = root / "tests" / "e2e" / "runners" / "run_cli_chat_probe.py"
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (f"import runpy; runpy.run_path({str(script)!r}, run_name='__test__')"),
        ],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
