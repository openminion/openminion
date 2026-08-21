from __future__ import annotations

import platform
import subprocess
import threading

from ..contracts import TargetPlatform, TransportResult
from ..interfaces import OutputSink

OUTPUT_LIMIT = 128 * 1024


def bounded(value: str) -> tuple[str, bool]:
    if len(value) <= OUTPUT_LIMIT:
        return value, False
    return value[:OUTPUT_LIMIT], True


def run_process(
    argv: tuple[str, ...],
    *,
    timeout_seconds: float,
    operation_id: str = "",
    active: dict[str, subprocess.Popen[str]] | None = None,
    lock: threading.RLock | None = None,
    output_sink: OutputSink | None = None,
    cwd: str = "",
) -> TransportResult:
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd or None,
        )
        if operation_id and active is not None and lock is not None:
            with lock:
                active[operation_id] = process
        stdout_raw, stderr_raw = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        if process is not None:
            process.kill()
            stdout_raw, stderr_raw = process.communicate()
        else:
            stdout_raw, stderr_raw = str(exc.stdout or ""), str(exc.stderr or "")
        stdout, stdout_cut = bounded(stdout_raw)
        stderr, stderr_cut = bounded(stderr_raw)
        return TransportResult(
            argv=argv,
            return_code=124,
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
            truncated=stdout_cut or stderr_cut,
        )
    except OSError as exc:
        stderr = str(exc)
        if output_sink is not None:
            output_sink("stderr", stderr)
        return TransportResult(argv=argv, return_code=127, stderr=stderr)
    finally:
        if operation_id and active is not None and lock is not None:
            with lock:
                active.pop(operation_id, None)
    stdout, stdout_cut = bounded(stdout_raw)
    stderr, stderr_cut = bounded(stderr_raw)
    if output_sink is not None:
        if stdout:
            output_sink("stdout", stdout)
        if stderr:
            output_sink("stderr", stderr)
    return TransportResult(
        argv=argv,
        return_code=process.returncode if process is not None else 1,
        stdout=stdout,
        stderr=stderr,
        truncated=stdout_cut or stderr_cut,
    )


class ProcessTransport:
    def __init__(self) -> None:
        self._active: dict[str, subprocess.Popen[str]] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.RLock()

    def _execute(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        operation_id: str,
        output_sink: OutputSink | None,
        cwd: str = "",
    ) -> TransportResult:
        result = run_process(
            argv,
            timeout_seconds=timeout_seconds,
            operation_id=operation_id,
            active=self._active,
            lock=self._lock,
            output_sink=output_sink,
            cwd=cwd,
        )
        if not operation_id:
            return result
        with self._lock:
            cancelled = operation_id in self._cancelled
            self._cancelled.discard(operation_id)
        if not cancelled:
            return result
        return result.model_copy(update={"cancelled": True, "return_code": 130})

    def cancel(self, operation_id: str) -> bool:
        with self._lock:
            process = self._active.get(operation_id)
            if process is None or process.poll() is not None:
                return False
            self._cancelled.add(operation_id)
            process.terminate()
            return True

    def close(self) -> None:
        with self._lock:
            operation_ids = tuple(self._active)
        for operation_id in operation_ids:
            self.cancel(operation_id)


def platform_name() -> TargetPlatform:
    return "darwin" if platform.system() == "Darwin" else "linux"
