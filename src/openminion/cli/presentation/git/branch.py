from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

__all__ = ["detect_branch"]


_TIMEOUT_SECONDS = 1.5


def detect_branch(working_dir: str | Path) -> str | None:
    raw = str(working_dir or "").strip()
    if not raw:
        return None
    try:
        path = Path(raw).expanduser()
    except (TypeError, ValueError):
        return None
    if not path.exists() or not path.is_dir():
        return None
    if shutil.which("git") is None:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    if not branch or branch == "HEAD":
        return None
    return branch
