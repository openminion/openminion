from dataclasses import dataclass, field
from threading import RLock
import time
from typing import cast
import uuid

from openminion.base.runtime.sandbox import ExecSpec, ExecutionSandboxSpec
from .client import DaytonaClient, DaytonaClientError

_SESSION_PREFIX = "execsandbox_"


@dataclass
class DaytonaSessionRecord:
    """In-memory mirror of a Daytona-backed remote session."""

    session_id: str
    remote_session_id: str
    workspace_id: str
    agent_id: str
    command: str
    cwd: str
    host: str
    shell_family: str
    use_pty: bool
    timeout_s: int
    started_at_unix: float
    deadline_unix: float
    exit_code: int | None = None
    killed: bool = False
    timed_out: bool = False
    stopped_at_unix: float = 0.0
    stdout_buffer: bytearray = field(default_factory=bytearray)
    stderr_buffer: bytearray = field(default_factory=bytearray)
    stdout_cursor: int = 0
    stderr_cursor: int = 0


class DaytonaSessionManager:
    """Runtime-owned manager that mirrors PROCESS_MANAGER semantics for sandbox sessions."""

    def __init__(self, *, client: DaytonaClient) -> None:
        self._client = client
        self._lock = RLock()
        self._sessions: dict[str, DaytonaSessionRecord] = {}

    def start(
        self,
        *,
        agent_id: str,
        command: str,
        cwd: str,
        env: dict[str, str],
        use_pty: bool,
        timeout_s: int,
        host: str,
        shell_family: str,
        exec_spec: ExecSpec,
        sandbox: ExecutionSandboxSpec,
    ) -> DaytonaSessionRecord:
        if not self._client.connected:
            self._client.open()
        now = time.time()
        session_id = f"{_SESSION_PREFIX}{uuid.uuid4().hex[:16]}"
        workspace = self._client.create_workspace(
            name=f"openminion-session-{uuid.uuid4().hex[:12]}",
            metadata={
                "workspace_root": sandbox.workspace_root,
                "read_allow": list(sandbox.read_allow),
                "write_allow": list(sandbox.write_allow),
                "delete_allow": list(sandbox.delete_allow),
                "cmd_allowlist": list(sandbox.cmd_allowlist),
                "env_allowlist": list(sandbox.env_allowlist),
                "timeout_s": sandbox.timeout_s,
                "max_output_bytes": sandbox.max_output_bytes,
                "address_space_bytes": sandbox.address_space_bytes,
                "cpu_seconds": sandbox.cpu_seconds,
                "session_mode": sandbox.session_mode,
                "net_mode": sandbox.net_mode,
                "allowed_domains": list(sandbox.allowed_domains),
                "idempotency_key": sandbox.idempotency_key,
                "pty": bool(use_pty),
            },
        )
        started = self._client.start_session(
            workspace_id=workspace.workspace_id,
            command=list(exec_spec.cmd),
            cwd=exec_spec.cwd,
            env=env,
            env_allowlist=sandbox.env_allowlist,
            timeout_s=sandbox.timeout_s,
            max_output_bytes=sandbox.max_output_bytes,
            use_pty=use_pty,
        )
        record = DaytonaSessionRecord(
            session_id=session_id,
            remote_session_id=started.session_id,
            workspace_id=workspace.workspace_id,
            agent_id=agent_id,
            command=command,
            cwd=cwd,
            host=host,
            shell_family=shell_family,
            use_pty=use_pty,
            timeout_s=int(timeout_s),
            started_at_unix=now,
            deadline_unix=now + float(timeout_s),
        )
        with self._lock:
            self._sessions[session_id] = record
        return record

    def wait(self, *, session_id: str, agent_id: str, wait_seconds: float) -> bool:
        deadline = time.time() + max(0.0, float(wait_seconds))
        while True:
            with self._lock:
                entry = self._sessions.get(session_id)
                if entry is None or entry.agent_id != agent_id:
                    return False
                self._refresh_locked(entry)
                if entry.exit_code is not None:
                    return True
            if time.time() >= deadline:
                return False
            time.sleep(0.05)

    def snapshot(
        self,
        *,
        session_id: str,
        agent_id: str,
    ) -> DaytonaSessionRecord | None:
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None or entry.agent_id != agent_id:
                return None
            self._refresh_locked(entry)
            return entry

    def consume_new_output(
        self,
        *,
        session_id: str,
        agent_id: str,
    ) -> tuple[bytes, bytes]:
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None or entry.agent_id != agent_id:
                return b"", b""
            self._refresh_locked(entry)
            stdout_new = bytes(entry.stdout_buffer[entry.stdout_cursor :])
            stderr_new = bytes(entry.stderr_buffer[entry.stderr_cursor :])
            entry.stdout_cursor = len(entry.stdout_buffer)
            entry.stderr_cursor = len(entry.stderr_buffer)
            return stdout_new, stderr_new

    def full_output(self, *, session_id: str, agent_id: str) -> tuple[bytes, bytes]:
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None or entry.agent_id != agent_id:
                return b"", b""
            self._refresh_locked(entry)
            return bytes(entry.stdout_buffer), bytes(entry.stderr_buffer)

    def send_input(
        self,
        *,
        session_id: str,
        agent_id: str,
        payload: bytes,
    ) -> tuple[bool, str]:
        if not payload:
            return True, "no-op"
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None or entry.agent_id != agent_id:
                return False, "session not found"
            self._refresh_locked(entry)
            if entry.exit_code is not None:
                return False, "session is not running"
            try:
                self._client.send_session_input(
                    workspace_id=entry.workspace_id,
                    session_id=entry.remote_session_id,
                    payload=payload,
                )
            except DaytonaClientError as exc:
                return False, exc.message
            self._refresh_locked(entry)
            return True, "input delivered"

    def kill(
        self,
        *,
        session_id: str,
        agent_id: str,
        signal_name: str | None,
    ) -> tuple[bool, str, DaytonaSessionRecord | None]:
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None or entry.agent_id != agent_id:
                return False, "session not found", None
            self._refresh_locked(entry)
            if entry.exit_code is not None:
                return True, "session already exited", entry
            try:
                result = self._client.terminate_session(
                    workspace_id=entry.workspace_id,
                    session_id=entry.remote_session_id,
                    signal_name=str(signal_name or "TERM").strip().upper() or "TERM",
                )
            except DaytonaClientError as exc:
                return False, exc.message, entry
            self._apply_poll_result_locked(entry, result.stdout, result.stderr, result)
            return True, "session terminated", entry

    def clear(self, *, session_id: str, agent_id: str) -> tuple[bool, str]:
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None or entry.agent_id != agent_id:
                return False, "session not found"
            self._refresh_locked(entry)
            if entry.exit_code is None:
                return False, "cannot clear a running session"
            self._sessions.pop(session_id, None)
            try:
                self._client.destroy_workspace(entry.workspace_id)
            except DaytonaClientError:
                return False, "failed to clear remote workspace"
            return True, "session cleared"

    def list(self, *, agent_id: str, include_exited: bool) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        with self._lock:
            for entry in self._sessions.values():
                if entry.agent_id != agent_id:
                    continue
                self._refresh_locked(entry)
                if entry.exit_code is not None and not include_exited:
                    continue
                rows.append(
                    {
                        "session_id": entry.session_id,
                        "status": self._status_locked(entry),
                        "exit_code": entry.exit_code,
                        "command": entry.command,
                        "cwd": entry.cwd,
                        "host": entry.host,
                        "shell_family": entry.shell_family,
                        "pty": entry.use_pty,
                        "started_at_unix": entry.started_at_unix,
                        "stopped_at_unix": entry.stopped_at_unix,
                    }
                )
        rows.sort(
            key=lambda row: cast(float, row["started_at_unix"]),
            reverse=True,
        )
        return rows

    def owns_session_id(self, session_id: str) -> bool:
        return str(session_id or "").startswith(_SESSION_PREFIX)

    def _refresh_locked(self, entry: DaytonaSessionRecord) -> None:
        result = self._client.poll_session(
            workspace_id=entry.workspace_id,
            session_id=entry.remote_session_id,
        )
        self._apply_poll_result_locked(entry, result.stdout, result.stderr, result)

    def _apply_poll_result_locked(
        self,
        entry: DaytonaSessionRecord,
        stdout: str,
        stderr: str,
        result: object,
    ) -> None:
        stdout_bytes = stdout.encode("utf-8", errors="replace")
        stderr_bytes = stderr.encode("utf-8", errors="replace")
        entry.stdout_buffer = bytearray(stdout_bytes)
        entry.stderr_buffer = bytearray(stderr_bytes)
        running = bool(getattr(result, "running", False))
        if running:
            return
        entry.exit_code = int(getattr(result, "exit_code", 0) or 0)
        entry.timed_out = bool(getattr(result, "timed_out", False))
        entry.killed = bool(getattr(result, "killed", False)) or entry.exit_code < 0
        entry.stopped_at_unix = time.time()

    def _status_locked(self, entry: DaytonaSessionRecord) -> str:
        if entry.exit_code is None:
            return "running"
        if entry.killed or entry.timed_out or entry.exit_code < 0:
            return "killed"
        return "exited"

    def _reset_for_tests(self) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            for entry in list(self._sessions.values()):
                try:
                    self._client.destroy_workspace(entry.workspace_id)
                except DaytonaClientError:
                    pass
            self._sessions.clear()


__all__ = [
    "DaytonaSessionManager",
    "DaytonaSessionRecord",
]
