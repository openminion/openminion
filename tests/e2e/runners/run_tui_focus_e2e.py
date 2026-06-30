from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


_ROOT = Path(__file__).resolve().parents[3]
_PYTHON = _ROOT / ".venv" / "bin" / "python3.11"


def _run(paths: list[str], *, env: dict[str, str]) -> int:
    command = [str(_PYTHON), "-m", "pytest", "-q", *paths, "-ra"]
    return subprocess.call(command, cwd=_ROOT, env=env)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    mode = args[0] if args else "local"
    env = os.environ.copy()
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    suites = {
        "local": ["tests/e2e/tui/focus/test_local.py"],
        "live": [
            "tests/e2e/tui/focus/test_live_basic.py",
            "tests/e2e/tui/focus/test_live_tools.py",
        ],
        "complex": ["tests/e2e/tui/focus/test_live_complex.py"],
        "all": ["tests/e2e/tui/focus"],
    }
    if mode not in suites:
        options = ", ".join(sorted(suites))
        print(f"usage: run_tui_focus_e2e.py [{options}]", file=sys.stderr)
        return 2
    if mode in {"live", "complex", "all"}:
        env["OPENMINION_LIVE_TUI_FOCUS_E2E"] = "1"
    if mode == "complex":
        env["OPENMINION_LIVE_TUI_FOCUS_COMPLEX_E2E"] = "1"
    return _run(suites[mode], env=env)


if __name__ == "__main__":
    raise SystemExit(main())
