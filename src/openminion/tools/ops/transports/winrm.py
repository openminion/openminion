from __future__ import annotations

import math
import threading
from collections.abc import Callable
from typing import Any, cast

from ..contracts import OperationTarget, TransportFacts, TransportResult, WinrmTarget
from ..interfaces import OutputSink
from .runtime import bounded
from .ssh import CredentialReader

ProtocolFactory = Callable[..., Any]


class WinrmTransport:
    def __init__(
        self,
        credential_reader: CredentialReader,
        *,
        protocol_factory: ProtocolFactory | None = None,
    ) -> None:
        self._credential_reader = credential_reader
        self._protocol_factory = protocol_factory
        self._active: dict[str, tuple[Any, str, str]] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.RLock()

    def connect(self, target: OperationTarget) -> TransportFacts:
        self._validate_target(target)
        return TransportFacts(
            kind="winrm",
            platform="windows",
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
        self._validate_target(target)
        winrm_target = cast(WinrmTarget, target)
        if not argv:
            raise ValueError("winrm command requires argv")
        assert winrm_target.credential_ref is not None
        protocol = self._new_protocol(
            endpoint=f"https://{winrm_target.address}:{winrm_target.port}/wsman",
            transport=winrm_target.winrm_auth_mode,
            username=winrm_target.username,
            password=self._credential_reader(winrm_target.credential_ref),
            ca_trust_path=winrm_target.ca_trust_path,
            server_cert_validation="validate",
            read_timeout_sec=math.ceil(timeout_seconds) + 5,
            operation_timeout_sec=math.ceil(timeout_seconds),
        )
        shell_id = protocol.open_shell(working_directory=cwd or None)
        command_id = protocol.run_command(shell_id, argv[0], list(argv[1:]))
        if operation_id:
            with self._lock:
                self._active[operation_id] = (protocol, shell_id, command_id)
        timed_out = False
        cancelled = False
        try:
            stdout_raw, stderr_raw, return_code = protocol.get_command_output(
                shell_id, command_id
            )
        except TimeoutError:
            stdout_raw, stderr_raw, return_code = b"", b"", 124
            timed_out = True
        except (OSError, RuntimeError, ValueError) as exc:
            with self._lock:
                cancelled = operation_id in self._cancelled
            if not cancelled:
                raise RuntimeError(
                    f"WinRM command failed: {type(exc).__name__}"
                ) from exc
            stdout_raw, stderr_raw, return_code = b"", b"", 130
        finally:
            if operation_id:
                with self._lock:
                    self._active.pop(operation_id, None)
                    self._cancelled.discard(operation_id)
            protocol.cleanup_command(shell_id, command_id)
            protocol.close_shell(shell_id)
        stdout, stdout_cut = bounded(stdout_raw.decode(errors="replace"))
        stderr, stderr_cut = bounded(stderr_raw.decode(errors="replace"))
        if output_sink is not None:
            if stdout:
                output_sink("stdout", stdout)
            if stderr:
                output_sink("stderr", stderr)
        return TransportResult(
            argv=argv,
            return_code=int(return_code),
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            cancelled=cancelled,
            truncated=stdout_cut or stderr_cut,
        )

    def cancel(self, operation_id: str) -> bool:
        with self._lock:
            active = self._active.get(operation_id)
        if active is None:
            return False
        protocol, shell_id, command_id = active
        with self._lock:
            self._cancelled.add(operation_id)
        protocol.cleanup_command(shell_id, command_id)
        return True

    def close(self) -> None:
        with self._lock:
            operation_ids = tuple(self._active)
        for operation_id in operation_ids:
            self.cancel(operation_id)

    def _new_protocol(self, **kwargs: object) -> Any:
        if self._protocol_factory is not None:
            return self._protocol_factory(**kwargs)
        try:
            from winrm.protocol import Protocol
        except ImportError as exc:
            raise RuntimeError(
                "WinRM operations require the optional 'remote-winrm' dependency"
            ) from exc
        return Protocol(**kwargs)

    @staticmethod
    def _validate_target(target: OperationTarget) -> None:
        if target.kind != "winrm":
            raise ValueError("winrm transport requires a winrm target")


__all__ = ["WinrmTransport"]
