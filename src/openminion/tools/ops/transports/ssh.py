from __future__ import annotations

import asyncio
import codecs
import shlex
import threading
import time
from collections.abc import Callable
from typing import NoReturn, Protocol, cast

from openminion.modules.runtime.credentials import CredentialRef
from openminion.modules.runtime.sync import run_async_compat

from ..contracts import (
    OperationTarget,
    TransportFacts,
    TransportResult,
)
from ..interfaces import OutputSink
from .runtime import OUTPUT_LIMIT


class _SshResult(Protocol):
    stdout: object
    stderr: object
    exit_status: int


class _SshReader(Protocol):
    async def read(self, count: int) -> bytes: ...


class _SshProcess(Protocol):
    stdout: _SshReader
    stderr: _SshReader
    exit_status: int

    async def wait(self, *, check: bool = False) -> _SshResult: ...


class _SshConnection(Protocol):
    async def create_process(
        self, command: str, *, encoding: object
    ) -> _SshProcess: ...

    def close(self) -> None: ...

    def abort(self) -> None: ...

    async def wait_closed(self) -> None: ...


CredentialReader = Callable[[CredentialRef], str]


class SshConnectionError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class _BoundedRedactedStream:
    def __init__(self, *, secret: str, sink: Callable[[str], None] | None) -> None:
        self._secret = secret
        self._sink = sink
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._pending = ""
        self._parts: list[str] = []
        self._size = 0
        self.truncated = False

    @property
    def value(self) -> str:
        return "".join(self._parts)

    def feed(self, chunk: bytes) -> None:
        if self.truncated:
            return
        self._pending += self._decoder.decode(chunk)
        self._redact_complete_matches()
        hold = self._partial_secret_suffix_length()
        if len(self._pending) > hold:
            end = len(self._pending) - hold if hold else len(self._pending)
            self._emit(self._pending[:end])
            self._pending = self._pending[end:]

    def finish(self) -> None:
        if self.truncated:
            return
        self._pending += self._decoder.decode(b"", final=True)
        self._redact_complete_matches()
        if self._partial_secret_suffix_length():
            self._pending = "[REDACTED]"
        self._emit(self._pending)
        self._pending = ""

    def _redact_complete_matches(self) -> None:
        if self._secret:
            self._pending = self._pending.replace(self._secret, "[REDACTED]")

    def _partial_secret_suffix_length(self) -> int:
        if not self._secret:
            return 0
        maximum = min(len(self._pending), len(self._secret) - 1)
        for size in range(maximum, 0, -1):
            if self._pending.endswith(self._secret[:size]):
                return size
        return 0

    def _emit(self, text: str) -> None:
        if not text:
            return
        remaining = OUTPUT_LIMIT - self._size
        encoded = text.encode("utf-8")
        if len(encoded) > remaining:
            text = encoded[:remaining].decode("utf-8", errors="ignore")
            encoded = text.encode("utf-8")
            self.truncated = True
        if text:
            self._parts.append(text)
            self._size += len(encoded)
            if self._sink is not None:
                self._sink(text)


class SshTransport:
    def __init__(self, credential_reader: CredentialReader) -> None:
        self._credential_reader = credential_reader
        self._active: dict[str, tuple[asyncio.AbstractEventLoop, _SshConnection]] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.RLock()

    def connect(self, target: OperationTarget) -> TransportFacts:
        return run_async_compat(self._probe_async(target))

    def inspect(self, target: OperationTarget) -> TransportFacts:
        return self.connect(target)

    async def _probe_async(self, target: OperationTarget) -> TransportFacts:
        self._validate_target(target)
        deadline = time.monotonic() + target.timeout_seconds
        connection = await self._open_connection(target, deadline=deadline)
        await self._close_connection(connection)
        return TransportFacts(
            kind="ssh",
            platform=target.platform,
            connected=True,
            capabilities=("command",),
        )

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
        self._validate_target(target)
        try:
            import asyncssh
        except ImportError as exc:
            raise SshConnectionError("dependency_missing") from exc
        deadline = time.monotonic() + timeout_seconds
        credential = self._read_credential(target)
        stdout = _BoundedRedactedStream(
            secret=credential,
            sink=(
                (lambda chunk: output_sink("stdout", chunk))
                if output_sink is not None
                else None
            ),
        )
        stderr = _BoundedRedactedStream(
            secret=credential,
            sink=(
                (lambda chunk: output_sink("stderr", chunk))
                if output_sink is not None
                else None
            ),
        )
        connection = await self._open_connection(
            target,
            deadline=deadline,
            credential=credential,
        )
        if operation_id:
            with self._lock:
                self._active[operation_id] = (
                    asyncio.get_running_loop(),
                    connection,
                )
        try:
            command = shlex.join(argv)
            if cwd:
                command = f"cd -- {shlex.quote(cwd)} && exec {command}"
            process = await asyncio.wait_for(
                connection.create_process(command, encoding=None),
                timeout=self._remaining(deadline),
            )
            result = await asyncio.wait_for(
                asyncio.gather(
                    self._drain(process.stdout, stdout),
                    self._drain(process.stderr, stderr),
                    process.wait(check=False),
                ),
                timeout=self._remaining(deadline),
            )
            stdout.finish()
            stderr.finish()
            with self._lock:
                cancelled = operation_id in self._cancelled
            if cancelled:
                return TransportResult(
                    argv=argv,
                    return_code=130,
                    stdout=stdout.value,
                    stderr=stderr.value,
                    cancelled=True,
                    truncated=stdout.truncated or stderr.truncated,
                )
        except asyncio.TimeoutError:
            stdout.finish()
            stderr.finish()
            return TransportResult(
                argv=argv,
                return_code=124,
                stdout=stdout.value,
                stderr=stderr.value,
                timed_out=True,
                truncated=stdout.truncated or stderr.truncated,
            )
        except (asyncssh.Error, OSError, RuntimeError) as exc:
            with self._lock:
                cancelled = operation_id in self._cancelled
            if not cancelled:
                raise RuntimeError(f"SSH command failed: {type(exc).__name__}") from exc
            stdout.finish()
            stderr.finish()
            return TransportResult(
                argv=argv,
                return_code=130,
                stdout=stdout.value,
                stderr=stderr.value,
                cancelled=True,
                truncated=stdout.truncated or stderr.truncated,
            )
        finally:
            if operation_id:
                with self._lock:
                    self._active.pop(operation_id, None)
                    self._cancelled.discard(operation_id)
            await self._close_connection(connection)
        completed = result[2]
        return TransportResult(
            argv=argv,
            return_code=int(completed.exit_status),
            stdout=stdout.value,
            stderr=stderr.value,
            truncated=stdout.truncated or stderr.truncated,
        )

    async def _open_connection(
        self,
        target: OperationTarget,
        *,
        deadline: float,
        credential: str | None = None,
    ) -> _SshConnection:
        try:
            import asyncssh
        except ImportError as exc:
            raise SshConnectionError("dependency_missing") from exc
        credential = (
            credential if credential is not None else self._read_credential(target)
        )
        try:
            password = credential if target.ssh_auth_mode == "password" else None
            client_keys: object = None
            if target.ssh_auth_mode == "private_key":
                client_keys = [asyncssh.import_private_key(credential)]
            known_hosts: object = target.endpoint_trust.known_hosts_path or None
            if target.endpoint_trust.host_key:
                host_key = asyncssh.import_public_key(target.endpoint_trust.host_key)
                known_hosts = ([host_key], [], [])
            return cast(
                _SshConnection,
                await asyncio.wait_for(
                    asyncssh.connect(
                        target.address,
                        port=target.port,
                        username=target.username or None,
                        password=password,
                        client_keys=client_keys,
                        known_hosts=known_hosts,
                        config=None,
                        agent_path=None,
                    ),
                    timeout=self._remaining(deadline),
                ),
            )
        except asyncio.TimeoutError as exc:
            raise SshConnectionError("timeout") from exc
        except asyncssh.HostKeyNotVerifiable as exc:
            raise SshConnectionError("host_key_mismatch") from exc
        except asyncssh.PermissionDenied as exc:
            raise SshConnectionError("authentication_failed") from exc
        except asyncssh.KeyImportError as exc:
            reason = (
                "credential_unavailable"
                if target.ssh_auth_mode == "private_key"
                else "host_key_mismatch"
            )
            raise SshConnectionError(reason) from exc
        except (asyncssh.Error, OSError, ValueError) as exc:
            raise SshConnectionError("connection_failed") from exc

    def _read_credential(self, target: OperationTarget) -> str:
        assert target.credential_ref is not None
        try:
            credential = self._credential_reader(target.credential_ref)
        except (KeyError, RuntimeError, ValueError) as exc:
            raise SshConnectionError("credential_unavailable") from exc
        if not credential:
            raise SshConnectionError("credential_unavailable")
        return credential

    @staticmethod
    async def _drain(reader: _SshReader, stream: _BoundedRedactedStream) -> None:
        while chunk := await reader.read(8192):
            stream.feed(chunk)

    @staticmethod
    async def _close_connection(connection: _SshConnection) -> None:
        connection.close()
        try:
            await asyncio.wait_for(connection.wait_closed(), timeout=1)
        except asyncio.TimeoutError:
            connection.abort()

    @staticmethod
    def _remaining(deadline: float) -> float:
        return max(0.001, deadline - time.monotonic())

    def read(
        self,
        target: OperationTarget,
        path: str,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> NoReturn:
        del target, path, max_bytes, timeout_seconds
        raise RuntimeError("remote_scope_unavailable")

    def cancel(self, operation_id: str) -> bool:
        with self._lock:
            active = self._active.get(operation_id)
        if active is None:
            return False
        loop, connection = active
        with self._lock:
            self._cancelled.add(operation_id)
        loop.call_soon_threadsafe(connection.close)
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
