from __future__ import annotations

import asyncio
import shlex
import threading
from collections.abc import Callable
from typing import Protocol, cast

from openminion.modules.runtime.credentials import CredentialRef
from openminion.modules.runtime.sync import run_async_compat

from ..contracts import (
    OperationTarget,
    TransportFacts,
    TransportReadResult,
    TransportResult,
)
from ..interfaces import OutputSink
from .runtime import OUTPUT_LIMIT, bounded


class _SshResult(Protocol):
    stdout: object
    stderr: object
    exit_status: int


class _SshConnection(Protocol):
    async def run(self, command: str, *, check: bool) -> _SshResult: ...

    def close(self) -> None: ...

    async def wait_closed(self) -> None: ...


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
        cwd: str = "",
    ) -> TransportResult:
        if target.kind != "ssh" or target.credential_ref is None:
            raise ValueError("ssh transport requires an ssh target")
        return run_async_compat(
            self._run_async(
                target,
                argv,
                timeout_seconds=timeout_seconds,
                operation_id=operation_id,
                output_sink=output_sink,
                cwd=cwd,
            )
        )

    async def _run_async(
        self,
        target: OperationTarget,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        operation_id: str,
        output_sink: OutputSink | None,
        cwd: str,
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
        password = credential if target.ssh_auth_mode == "password" else None
        client_keys: object = None
        if target.ssh_auth_mode == "private_key":
            client_keys = [asyncssh.import_private_key(credential)]
        known_hosts: object = target.endpoint_trust.known_hosts_path or None
        if target.endpoint_trust.host_key:
            host_key = asyncssh.import_public_key(target.endpoint_trust.host_key)
            known_hosts = ([host_key], [], [])
        try:
            connection = cast(
                _SshConnection,
                await asyncssh.connect(
                    target.address,
                    port=target.port,
                    username=target.username or None,
                    password=password,
                    client_keys=client_keys,
                    known_hosts=known_hosts,
                    config=None,
                    agent_path=None,
                ),
            )
        except (asyncssh.Error, OSError, ValueError) as exc:
            raise RuntimeError(f"SSH connection failed: {type(exc).__name__}") from exc
        if operation_id:
            with self._lock:
                self._active[operation_id] = connection
        try:
            command = shlex.join(argv)
            if cwd:
                command = f"cd -- {shlex.quote(cwd)} && exec {command}"
            result = await asyncio.wait_for(
                connection.run(command, check=False),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            return TransportResult(argv=argv, return_code=124, timed_out=True)
        except (asyncssh.Error, OSError) as exc:
            with self._lock:
                cancelled = operation_id in self._cancelled
            if not cancelled:
                raise RuntimeError(f"SSH command failed: {type(exc).__name__}") from exc
            return TransportResult(argv=argv, return_code=130, cancelled=True)
        finally:
            if operation_id:
                with self._lock:
                    self._active.pop(operation_id, None)
                    self._cancelled.discard(operation_id)
            connection.close()
            await connection.wait_closed()
        stdout, stdout_cut = bounded(str(result.stdout or ""))
        stderr, stderr_cut = bounded(str(result.stderr or ""))
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
        if max_bytes < 1 or max_bytes > OUTPUT_LIMIT:
            raise ValueError(f"max_bytes must be between 1 and {OUTPUT_LIMIT}")
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
