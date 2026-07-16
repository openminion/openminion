from __future__ import annotations

import asyncio
import platform
import shlex
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

from openminion.modules.runtime.credentials import CredentialRef
from openminion.modules.runtime.sync import run_async_compat

from .interfaces import OutputSink
from .contracts import (
    OperationTarget,
    TargetPlatform,
    TransportFacts,
    TransportReadResult,
    TransportResult,
)

_OUTPUT_LIMIT = 128 * 1024


class _SshResult(Protocol):
    stdout: object
    stderr: object
    exit_status: int


class _SshConnection(Protocol):
    async def run(self, command: str, *, check: bool) -> _SshResult: ...

    def close(self) -> None: ...

    async def wait_closed(self) -> None: ...


def _bounded(value: str) -> tuple[str, bool]:
    if len(value) <= _OUTPUT_LIMIT:
        return value, False
    return value[:_OUTPUT_LIMIT], True


def _run(
    argv: tuple[str, ...],
    *,
    timeout_seconds: float,
    operation_id: str = "",
    active: dict[str, subprocess.Popen[str]] | None = None,
    lock: threading.RLock | None = None,
    output_sink: OutputSink | None = None,
) -> TransportResult:
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
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
        stdout, stdout_cut = _bounded(stdout_raw)
        stderr, stderr_cut = _bounded(stderr_raw)
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
        return TransportResult(
            argv=argv,
            return_code=127,
            stderr=stderr,
        )
    finally:
        if operation_id and active is not None and lock is not None:
            with lock:
                active.pop(operation_id, None)
    stdout, stdout_cut = _bounded(stdout_raw)
    stderr, stderr_cut = _bounded(stderr_raw)
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


class _ProcessTransport:
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
    ) -> TransportResult:
        result = _run(
            argv,
            timeout_seconds=timeout_seconds,
            operation_id=operation_id,
            active=self._active,
            lock=self._lock,
            output_sink=output_sink,
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


def _platform_name() -> TargetPlatform:
    return "darwin" if platform.system() == "Darwin" else "linux"


class LocalTransport(_ProcessTransport):
    def connect(self, target: OperationTarget) -> TransportFacts:
        if target.kind != "local":
            raise ValueError("local transport requires a local target")
        return TransportFacts(
            kind="local",
            platform=_platform_name(),
            connected=True,
            capabilities=target.capabilities,
        )

    def inspect(self, target: OperationTarget) -> TransportFacts:
        return self.connect(target)

    def run(
        self,
        target: OperationTarget,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        operation_id: str = "",
        output_sink: OutputSink | None = None,
    ) -> TransportResult:
        if target.kind != "local":
            raise ValueError("local transport requires a local target")
        return self._execute(
            argv,
            timeout_seconds=timeout_seconds,
            operation_id=operation_id,
            output_sink=output_sink,
        )

    def read(
        self,
        target: OperationTarget,
        path: str,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> TransportReadResult:
        del timeout_seconds
        self.connect(target)
        if max_bytes < 1 or max_bytes > _OUTPUT_LIMIT:
            raise ValueError(f"max_bytes must be between 1 and {_OUTPUT_LIMIT}")
        raw = Path(path).read_bytes()
        return TransportReadResult(
            path=path,
            content=raw[:max_bytes].decode(errors="replace"),
            truncated=len(raw) > max_bytes,
        )


class ContainerTransport(_ProcessTransport):
    def __init__(self, runtime: str = "docker") -> None:
        super().__init__()
        if runtime not in {"docker", "podman"}:
            raise ValueError("container runtime must be docker or podman")
        self.runtime = runtime

    def connect(self, target: OperationTarget) -> TransportFacts:
        if target.kind != "container":
            raise ValueError("container transport requires a container target")
        return TransportFacts(
            kind="container",
            platform=target.platform,
            connected=True,
            capabilities=target.capabilities,
        )

    def inspect(self, target: OperationTarget) -> TransportFacts:
        return self.connect(target)

    def run(
        self,
        target: OperationTarget,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        operation_id: str = "",
        output_sink: OutputSink | None = None,
    ) -> TransportResult:
        if target.kind != "container":
            raise ValueError("container transport requires a container target")
        return self._execute(
            (self.runtime, "exec", target.container, *argv),
            timeout_seconds=timeout_seconds,
            operation_id=operation_id,
            output_sink=output_sink,
        )

    def read(
        self,
        target: OperationTarget,
        path: str,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> TransportReadResult:
        if max_bytes < 1 or max_bytes > _OUTPUT_LIMIT:
            raise ValueError(f"max_bytes must be between 1 and {_OUTPUT_LIMIT}")
        result = self.run(
            target,
            ("head", "-c", str(max_bytes + 1), "--", path),
            timeout_seconds=timeout_seconds,
        )
        if result.return_code != 0:
            raise OSError(result.stderr or f"unable to read {path}")
        return TransportReadResult(
            path=path,
            content=result.stdout[:max_bytes],
            truncated=len(result.stdout) > max_bytes,
        )


CredentialReader = Callable[[CredentialRef], str]


class SshTransport:
    def __init__(self, credential_reader: CredentialReader) -> None:
        self._credential_reader = credential_reader
        self._active: dict[str, _SshConnection] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.RLock()

    def connect(self, target: OperationTarget) -> TransportFacts:
        self._validate_target(target)
        return TransportFacts(
            kind="ssh",
            platform=target.platform,
            connected=True,
            capabilities=target.capabilities,
        )

    def inspect(self, target: OperationTarget) -> TransportFacts:
        return self.connect(target)

    def run(
        self,
        target: OperationTarget,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        operation_id: str = "",
        output_sink: OutputSink | None = None,
    ) -> TransportResult:
        if target.kind != "ssh" or target.credential_ref is None:
            raise ValueError("ssh transport requires an ssh target")
        result: TransportResult = run_async_compat(
            self._run_async(
                target,
                argv,
                timeout_seconds=timeout_seconds,
                operation_id=operation_id,
                output_sink=output_sink,
            )
        )
        return result

    async def _run_async(
        self,
        target: OperationTarget,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        operation_id: str,
        output_sink: OutputSink | None,
    ) -> TransportResult:
        try:
            import asyncssh
        except ImportError as exc:
            raise RuntimeError(
                "SSH operations require the optional 'remote' dependency"
            ) from exc
        self._validate_target(target)
        assert target.credential_ref is not None
        credential = self._credential_reader(target.credential_ref)
        known_hosts: object = target.endpoint_trust.known_hosts_path or None
        if target.endpoint_trust.host_key:
            host_key = asyncssh.import_public_key(target.endpoint_trust.host_key)
            known_hosts = ([host_key], [], [])
        connection = cast(
            _SshConnection,
            await asyncssh.connect(
                target.address,
                port=target.port,
                username=target.username or None,
                password=credential,
                client_keys=None,
                known_hosts=known_hosts,
            ),
        )
        if operation_id:
            with self._lock:
                self._active[operation_id] = connection
        try:
            result = await asyncio.wait_for(
                connection.run(shlex.join(argv), check=False),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            return TransportResult(
                argv=argv,
                return_code=124,
                timed_out=True,
            )
        except Exception:
            with self._lock:
                cancelled = operation_id in self._cancelled
            if not cancelled:
                raise
            return TransportResult(
                argv=argv,
                return_code=130,
                cancelled=True,
            )
        finally:
            if operation_id:
                with self._lock:
                    self._active.pop(operation_id, None)
                    self._cancelled.discard(operation_id)
            connection.close()
            await connection.wait_closed()
        stdout, stdout_cut = _bounded(str(result.stdout or ""))
        stderr, stderr_cut = _bounded(str(result.stderr or ""))
        if output_sink is not None:
            if stdout:
                output_sink("stdout", stdout)
            if stderr:
                output_sink("stderr", stderr)
        return TransportResult(
            argv=argv,
            return_code=int(result.exit_status),
            stdout=stdout,
            stderr=stderr,
            truncated=stdout_cut or stderr_cut,
        )

    def read(
        self,
        target: OperationTarget,
        path: str,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> TransportReadResult:
        if max_bytes < 1 or max_bytes > _OUTPUT_LIMIT:
            raise ValueError(f"max_bytes must be between 1 and {_OUTPUT_LIMIT}")
        result = self.run(
            target,
            ("head", "-c", str(max_bytes + 1), "--", path),
            timeout_seconds=timeout_seconds,
        )
        if result.return_code != 0:
            raise OSError(result.stderr or f"unable to read {path}")
        return TransportReadResult(
            path=path,
            content=result.stdout[:max_bytes],
            truncated=len(result.stdout) > max_bytes,
        )

    def cancel(self, operation_id: str) -> bool:
        with self._lock:
            connection = self._active.get(operation_id)
        if connection is None:
            return False
        with self._lock:
            self._cancelled.add(operation_id)
        connection.close()
        return True

    def close(self) -> None:
        with self._lock:
            operation_ids = tuple(self._active)
        for operation_id in operation_ids:
            self.cancel(operation_id)

    @staticmethod
    def _validate_target(target: OperationTarget) -> None:
        if target.kind != "ssh" or target.credential_ref is None:
            raise ValueError("ssh transport requires an ssh target")
