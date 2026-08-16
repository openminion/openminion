"""Fixed-argv scanner process execution with bounded output."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

_OUTPUT_LIMIT = 2 * 1024 * 1024


@dataclass(frozen=True)
class ScannerProcessResult:
    return_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False


def run_scanner(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: float,
) -> ScannerProcessResult:
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed adapter-owned argv
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        stdout, stdout_cut = _bounded(stdout)
        stderr, stderr_cut = _bounded(stderr)
        return ScannerProcessResult(
            return_code=124,
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
            truncated=stdout_cut or stderr_cut,
        )
    except OSError as exc:
        return ScannerProcessResult(return_code=127, stderr=str(exc))

    stdout, stdout_cut = _bounded(stdout)
    stderr, stderr_cut = _bounded(stderr)
    return_code = process.returncode
    return ScannerProcessResult(
        return_code=return_code,
        stdout=stdout,
        stderr=stderr,
        cancelled=return_code in {130, -2, -15},
        truncated=stdout_cut or stderr_cut,
    )


def _bounded(value: str) -> tuple[str, bool]:
    encoded = value.encode(errors="replace")
    if len(encoded) <= _OUTPUT_LIMIT:
        return value, False
    return encoded[:_OUTPUT_LIMIT].decode(errors="replace"), True


__all__ = ["ScannerProcessResult", "run_scanner"]
