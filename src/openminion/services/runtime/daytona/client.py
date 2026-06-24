"""Typed Daytona client wrapper for runtime sandbox execution."""

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .config import DaytonaConfig
from openminion.services.runtime.constants import (
    SANDBOX_ERROR_CODES,
    SANDBOX_NETWORK_DENIED,
    SANDBOX_RESOURCE_LIMIT,
    SANDBOX_UNAVAILABLE,
)


@dataclass(frozen=True)
class DaytonaWorkspace:
    """Stable workspace metadata returned by the Daytona client."""

    workspace_id: str
    name: str
    image: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DaytonaCommandResult:
    """Normalized command execution result from the Daytona client."""

    workspace_id: str
    returncode: int
    stdout: str
    stderr: str
    truncated: bool = False
    timed_out: bool = False


@dataclass(frozen=True)
class DaytonaSessionStartResult:
    """Normalized remote session creation result from the Daytona client."""

    workspace_id: str
    session_id: str


@dataclass(frozen=True)
class DaytonaSessionPollResult:
    """Normalized remote session poll result from the Daytona client."""

    workspace_id: str
    session_id: str
    running: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    timed_out: bool = False
    killed: bool = False


@dataclass
class DaytonaTransportError(RuntimeError):
    """Transport-owned error raised below the client boundary."""

    code: str
    message: str
    status_code: int | None = None
    retryable: bool = False
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass
class DaytonaClientError(RuntimeError):
    """Typed sandbox-facing error raised by the Daytona client."""

    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class DaytonaTransport(Protocol):
    """Transport seam for the Daytona client."""

    def open(self, config: DaytonaConfig, *, api_key: str) -> None: ...

    def close(self) -> None: ...

    def create_workspace(
        self,
        *,
        name: str,
        image: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...

    def destroy_workspace(self, workspace_id: str) -> None: ...

    def execute_command(
        self,
        *,
        workspace_id: str,
        command: list[str],
        cwd: str | None,
        env: Mapping[str, str],
        timeout_s: float,
        max_output_bytes: int,
    ) -> Mapping[str, Any]: ...

    def start_session(
        self,
        *,
        workspace_id: str,
        command: list[str],
        cwd: str | None,
        env: Mapping[str, str],
        timeout_s: float,
        max_output_bytes: int,
        use_pty: bool,
    ) -> Mapping[str, Any]: ...

    def poll_session(
        self,
        *,
        workspace_id: str,
        session_id: str,
        max_output_bytes: int,
    ) -> Mapping[str, Any]: ...

    def send_session_input(
        self,
        *,
        workspace_id: str,
        session_id: str,
        payload: bytes,
    ) -> Mapping[str, Any] | None: ...

    def terminate_session(
        self,
        *,
        workspace_id: str,
        session_id: str,
        signal_name: str,
    ) -> Mapping[str, Any] | None: ...


class _UnavailableDaytonaTransport:
    """Default transport stub until a real Daytona transport is configured."""

    def open(self, config: DaytonaConfig, *, api_key: str) -> None:
        return None

    def close(self) -> None:
        return None

    def create_workspace(
        self,
        *,
        name: str,
        image: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        raise DaytonaTransportError(
            code="UNAVAILABLE",
            message="Daytona transport is not configured",
            retryable=False,
        )

    def destroy_workspace(self, workspace_id: str) -> None:
        raise DaytonaTransportError(
            code="UNAVAILABLE",
            message="Daytona transport is not configured",
            retryable=False,
        )

    def execute_command(
        self,
        *,
        workspace_id: str,
        command: list[str],
        cwd: str | None,
        env: Mapping[str, str],
        timeout_s: float,
        max_output_bytes: int,
    ) -> Mapping[str, Any]:
        raise DaytonaTransportError(
            code="UNAVAILABLE",
            message="Daytona transport is not configured",
            retryable=False,
        )

    def start_session(
        self,
        *,
        workspace_id: str,
        command: list[str],
        cwd: str | None,
        env: Mapping[str, str],
        timeout_s: float,
        max_output_bytes: int,
        use_pty: bool,
    ) -> Mapping[str, Any]:
        del workspace_id, command, cwd, env, timeout_s, max_output_bytes, use_pty
        raise DaytonaTransportError(
            code="UNAVAILABLE",
            message="Daytona transport is not configured",
            retryable=False,
        )

    def poll_session(
        self,
        *,
        workspace_id: str,
        session_id: str,
        max_output_bytes: int,
    ) -> Mapping[str, Any]:
        del workspace_id, session_id, max_output_bytes
        raise DaytonaTransportError(
            code="UNAVAILABLE",
            message="Daytona transport is not configured",
            retryable=False,
        )

    def send_session_input(
        self,
        *,
        workspace_id: str,
        session_id: str,
        payload: bytes,
    ) -> Mapping[str, Any] | None:
        del workspace_id, session_id, payload
        raise DaytonaTransportError(
            code="UNAVAILABLE",
            message="Daytona transport is not configured",
            retryable=False,
        )

    def terminate_session(
        self,
        *,
        workspace_id: str,
        session_id: str,
        signal_name: str,
    ) -> Mapping[str, Any] | None:
        del workspace_id, session_id, signal_name
        raise DaytonaTransportError(
            code="UNAVAILABLE",
            message="Daytona transport is not configured",
            retryable=False,
        )


def _truncate_text(
    stdout: str,
    stderr: str,
    *,
    max_output_bytes: int,
) -> tuple[str, str, bool]:
    if max_output_bytes <= 0:
        return "", "", bool(stdout or stderr)

    stdout_bytes = stdout.encode("utf-8", errors="replace")
    stderr_bytes = stderr.encode("utf-8", errors="replace")
    total = len(stdout_bytes) + len(stderr_bytes)
    if total <= max_output_bytes:
        return stdout, stderr, False

    stdout_budget = min(len(stdout_bytes), max_output_bytes)
    stdout_slice = stdout_bytes[:stdout_budget]
    stdout_trimmed = stdout_slice.decode("utf-8", errors="ignore")
    remaining = max(0, max_output_bytes - len(stdout_slice))
    if remaining > 0:
        stderr_trimmed = stderr_bytes[:remaining].decode("utf-8", errors="ignore")
    else:
        stderr_trimmed = ""
    return stdout_trimmed, stderr_trimmed, True


def _filter_env(
    env: Mapping[str, str] | None,
    *,
    env_allowlist: list[str] | tuple[str, ...] | None,
) -> dict[str, str]:
    source = dict(env or {})
    allow = {str(name).strip() for name in (env_allowlist or []) if str(name).strip()}
    if not allow:
        return {}
    return {key: value for key, value in source.items() if key in allow}


class DaytonaClient:
    """Typed wrapper over a Daytona transport implementation."""

    def __init__(
        self,
        *,
        config: DaytonaConfig,
        transport: DaytonaTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or _UnavailableDaytonaTransport()
        self._connected = False

    @property
    def config(self) -> DaytonaConfig:
        return self._config

    @property
    def connected(self) -> bool:
        return self._connected

    def open(self) -> None:
        api_key = self._config.resolve_api_key()
        try:
            self._transport.open(self._config, api_key=api_key)
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc, operation="open") from exc
        self._connected = True

    def close(self) -> None:
        try:
            self._transport.close()
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc, operation="close") from exc
        self._connected = False

    def refresh(self) -> None:
        if self._connected:
            self.close()
        self.open()

    def create_workspace(
        self,
        *,
        name: str,
        image: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> DaytonaWorkspace:
        try:
            raw = self._transport.create_workspace(
                name=str(name or "").strip(),
                image=str(image or self._config.default_workspace_image).strip(),
                metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc, operation="create_workspace") from exc
        workspace_id = str(raw.get("workspace_id", "") or "").strip()
        if not workspace_id:
            raise DaytonaClientError(
                code=SANDBOX_UNAVAILABLE,
                message="Daytona workspace response missing workspace_id",
                retryable=False,
            )
        return DaytonaWorkspace(
            workspace_id=workspace_id,
            name=str(raw.get("name", name) or "").strip(),
            image=str(
                raw.get("image", image or self._config.default_workspace_image) or ""
            ).strip(),
            metadata=dict(raw.get("metadata") or {}),
        )

    def destroy_workspace(self, workspace_id: str) -> None:
        try:
            self._transport.destroy_workspace(str(workspace_id or "").strip())
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc, operation="destroy_workspace") from exc

    def execute_command(
        self,
        *,
        workspace_id: str,
        command: list[str],
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        env_allowlist: list[str] | tuple[str, ...] | None = None,
        timeout_s: float | None = None,
        max_output_bytes: int | None = None,
    ) -> DaytonaCommandResult:
        if not command:
            raise DaytonaClientError(
                code=SANDBOX_UNAVAILABLE,
                message="Daytona command cannot be empty",
                retryable=False,
            )
        filtered_env = _filter_env(env, env_allowlist=env_allowlist)
        timeout = float(timeout_s or self._config.command_timeout_s)
        output_cap = int(max_output_bytes or self._config.max_output_bytes)
        try:
            raw = self._transport.execute_command(
                workspace_id=str(workspace_id or "").strip(),
                command=list(command),
                cwd=cwd,
                env=filtered_env,
                timeout_s=timeout,
                max_output_bytes=output_cap,
            )
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc, operation="execute_command") from exc
        stdout = str(raw.get("stdout", "") or "")
        stderr = str(raw.get("stderr", "") or "")
        stdout, stderr, truncated = _truncate_text(
            stdout,
            stderr,
            max_output_bytes=output_cap,
        )
        timed_out = bool(raw.get("timed_out", False))
        return DaytonaCommandResult(
            workspace_id=str(raw.get("workspace_id", workspace_id) or "").strip(),
            returncode=int(raw.get("returncode", 0)),
            stdout=stdout,
            stderr=stderr,
            truncated=truncated or bool(raw.get("truncated", False)),
            timed_out=timed_out,
        )

    def start_session(
        self,
        *,
        workspace_id: str,
        command: list[str],
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        env_allowlist: list[str] | tuple[str, ...] | None = None,
        timeout_s: float | None = None,
        max_output_bytes: int | None = None,
        use_pty: bool = False,
    ) -> DaytonaSessionStartResult:
        if not command:
            raise DaytonaClientError(
                code=SANDBOX_UNAVAILABLE,
                message="Daytona session command cannot be empty",
                retryable=False,
            )
        filtered_env = _filter_env(env, env_allowlist=env_allowlist)
        timeout = float(timeout_s or self._config.command_timeout_s)
        output_cap = int(max_output_bytes or self._config.max_output_bytes)
        try:
            raw = self._transport.start_session(
                workspace_id=str(workspace_id or "").strip(),
                command=list(command),
                cwd=cwd,
                env=filtered_env,
                timeout_s=timeout,
                max_output_bytes=output_cap,
                use_pty=bool(use_pty),
            )
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc, operation="start_session") from exc
        remote_session_id = str(raw.get("session_id", "") or "").strip()
        if not remote_session_id:
            raise DaytonaClientError(
                code=SANDBOX_UNAVAILABLE,
                message="Daytona session response missing session_id",
                retryable=False,
            )
        return DaytonaSessionStartResult(
            workspace_id=str(raw.get("workspace_id", workspace_id) or "").strip(),
            session_id=remote_session_id,
        )

    def poll_session(
        self,
        *,
        workspace_id: str,
        session_id: str,
        max_output_bytes: int | None = None,
    ) -> DaytonaSessionPollResult:
        output_cap = int(max_output_bytes or self._config.max_output_bytes)
        try:
            raw = self._transport.poll_session(
                workspace_id=str(workspace_id or "").strip(),
                session_id=str(session_id or "").strip(),
                max_output_bytes=output_cap,
            )
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc, operation="poll_session") from exc
        stdout = str(raw.get("stdout", "") or "")
        stderr = str(raw.get("stderr", "") or "")
        stdout, stderr, truncated = _truncate_text(
            stdout,
            stderr,
            max_output_bytes=output_cap,
        )
        running = bool(raw.get("running", False))
        return DaytonaSessionPollResult(
            workspace_id=str(raw.get("workspace_id", workspace_id) or "").strip(),
            session_id=str(raw.get("session_id", session_id) or "").strip(),
            running=running,
            exit_code=None if running else int(raw.get("exit_code", 0)),
            stdout=stdout,
            stderr=stderr,
            truncated=truncated or bool(raw.get("truncated", False)),
            timed_out=bool(raw.get("timed_out", False)),
            killed=bool(raw.get("killed", False)),
        )

    def send_session_input(
        self,
        *,
        workspace_id: str,
        session_id: str,
        payload: bytes,
    ) -> None:
        try:
            self._transport.send_session_input(
                workspace_id=str(workspace_id or "").strip(),
                session_id=str(session_id or "").strip(),
                payload=bytes(payload),
            )
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc, operation="send_session_input") from exc

    def terminate_session(
        self,
        *,
        workspace_id: str,
        session_id: str,
        signal_name: str,
    ) -> DaytonaSessionPollResult:
        try:
            raw = self._transport.terminate_session(
                workspace_id=str(workspace_id or "").strip(),
                session_id=str(session_id or "").strip(),
                signal_name=str(signal_name or "TERM").strip().upper() or "TERM",
            )
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc, operation="terminate_session") from exc
        raw = dict(raw or {})
        return DaytonaSessionPollResult(
            workspace_id=str(raw.get("workspace_id", workspace_id) or "").strip(),
            session_id=str(raw.get("session_id", session_id) or "").strip(),
            running=bool(raw.get("running", False)),
            exit_code=raw.get("exit_code"),
            stdout=str(raw.get("stdout", "") or ""),
            stderr=str(raw.get("stderr", "") or ""),
            truncated=bool(raw.get("truncated", False)),
            timed_out=bool(raw.get("timed_out", False)),
            killed=bool(raw.get("killed", False)),
        )

    def _map_error(self, exc: Exception, *, operation: str) -> DaytonaClientError:
        if isinstance(exc, DaytonaClientError):
            return exc
        if isinstance(exc, TimeoutError):
            return DaytonaClientError(
                code=SANDBOX_RESOURCE_LIMIT,
                message=f"Daytona {operation} timed out",
                retryable=True,
            )
        if isinstance(exc, DaytonaTransportError):
            code = self._normalize_transport_code(exc)
            return DaytonaClientError(
                code=code,
                message=exc.message,
                retryable=exc.retryable,
                details=exc.details,
            )
        return DaytonaClientError(
            code=SANDBOX_UNAVAILABLE,
            message=f"Daytona {operation} failed: {exc}",
            retryable=False,
        )

    def _normalize_transport_code(self, exc: DaytonaTransportError) -> str:
        normalized = str(exc.code or "").strip().upper()
        if normalized in SANDBOX_ERROR_CODES:
            return normalized
        if normalized in {"TIMEOUT", "DEADLINE_EXCEEDED", "RESOURCE_LIMIT"}:
            return SANDBOX_RESOURCE_LIMIT
        if normalized in {"NETWORK_DENIED", "EGRESS_DENIED"}:
            return SANDBOX_NETWORK_DENIED
        if (
            exc.status_code == 403
            and str((exc.details or {}).get("reason", "")).strip()
        ):
            reason = str((exc.details or {}).get("reason", "")).strip().lower()
            if "network" in reason or "egress" in reason:
                return SANDBOX_NETWORK_DENIED
        return SANDBOX_UNAVAILABLE


__all__ = [
    "DaytonaClient",
    "DaytonaClientError",
    "DaytonaCommandResult",
    "DaytonaConfig",
    "DaytonaSessionPollResult",
    "DaytonaSessionStartResult",
    "DaytonaTransport",
    "DaytonaTransportError",
    "DaytonaWorkspace",
]
