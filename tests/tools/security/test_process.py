import sys
from pathlib import Path

from openminion.tools.security.process import run_scanner


def test_scanner_process_timeout_and_cancellation(tmp_path: Path) -> None:
    timed_out = run_scanner(
        (sys.executable, "-c", "import time; time.sleep(2)"),
        cwd=tmp_path,
        timeout_seconds=0.01,
    )
    cancelled = run_scanner(
        (sys.executable, "-c", "raise SystemExit(130)"),
        cwd=tmp_path,
        timeout_seconds=2,
    )
    assert timed_out.timed_out is True
    assert timed_out.return_code == 124
    assert cancelled.cancelled is True
    assert cancelled.return_code == 130
