from __future__ import annotations

import subprocess
import sys


def test_executor_is_directly_importable_without_circular_dependency() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import openminion.modules.tool.executor; from openminion.modules.tool.executor import ToolExecutionBatch",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Importing executor failed:\n{result.stderr}"
