#!/usr/bin/env python3.11
from __future__ import annotations

from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.e2e.runners.run_cli_focus_e2e import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
