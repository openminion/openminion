import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict

from openminion.base.config.env import resolve_environment_config
from openminion.base.config.env.subprocess import build_subprocess_env

if os.name != "nt":
    import fcntl
    import pty


_READ_CHUNK_SIZE = 64 * 1024


class ShellFamily(str, Enum):
    POSIX = "posix"
    POWERSHELL = "powershell"
    CMD = "cmd"
    UNKNOWN = "unknown"


def _is_windows_platform() -> bool:
    return os.name == "nt"


def _set_nonblocking(fd: int) -> None:
    if _is_windows_platform():
        return
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


def _open_session_pty() -> tuple[int, int]:
    openpty = getattr(os, "openpty", None)
    if callable(openpty):
        return openpty()
    return pty.openpty()


def _select_shell(command: str) -> tuple[list[str], ShellFamily]:
    if _is_windows_platform():
        for candidate in ("pwsh", "powershell"):
            located = shutil.which(candidate)
            if located:
                return (
                    [located, "-NoLogo", "-NoProfile", "-Command", command],
                    ShellFamily.POWERSHELL,
                )
        return (["cmd.exe", "/c", command], ShellFamily.CMD)

    env_shell = resolve_environment_config().get("SHELL", "").strip()
    if (
        env_shell
        and Path(env_shell).name.lower() != "fish"
        and Path(env_shell).exists()
    ):
        return ([env_shell, "-lc", command], ShellFamily.POSIX)

    for candidate in (
        "/bin/bash",
        "/usr/bin/bash",
        "/bin/zsh",
        "/usr/bin/zsh",
        "/bin/sh",
        "/usr/bin/sh",
    ):
        shell_path = Path(candidate)
        if shell_path.exists() and shell_path.name.lower() != "fish":
            return ([candidate, "-lc", command], ShellFamily.POSIX)
    return (["sh", "-lc", command], ShellFamily.POSIX)


def resolve_shell_family() -> ShellFamily:
    _argv, shell_family = _select_shell("")
    return shell_family


@dataclass
class SessionRecord:
    session_id: str
    agent_id: str
    command: str
    cwd: str
    host: str
    shell_family: str
    use_pty: bool
    timeout_s: int
    started_at_unix: float
    deadline_unix: float
    process: subprocess.Popen[bytes] | None
    stdin_pipe: Any = None
    stdout_pipe: Any = None
    stderr_pipe: Any = None
    pty_master_fd: int | None = None
    exit_code: int | None = None
    killed: bool = False
    timed_out: bool = False
    stopped_at_unix: float = 0.0
    stdout_buffer: bytearray = field(default_factory=bytearray)
    stderr_buffer: bytearray = field(default_factory=bytearray)
    stdout_cursor: int = 0
    stderr_cursor: int = 0


class ProcessManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[str, SessionRecord] = {}

    def start(
        self,
        *,
        agent_id: str,
        command: str,
        cwd: str,
        env: Dict[str, str],
        use_pty: bool,
        timeout_s: int,
        host: str,
    ) -> SessionRecord:
        if use_pty and _is_windows_platform():
            raise RuntimeError("PTY execution is not supported on Windows")

        shell_argv, shell_family = _select_shell(command)
        process_env = build_subprocess_env(overlay=env)

        now = time.time()
        session_id = f"execproc_{uuid.uuid4().hex[:16]}"

        proc: subprocess.Popen[bytes]
        stdin_pipe = None
        stdout_pipe = None
        stderr_pipe = None
        pty_master_fd: int | None = None

        if use_pty:
            master_fd, slave_fd = _open_session_pty()
            try:
                proc = subprocess.Popen(
                    shell_argv,
                    cwd=cwd,
                    env=process_env,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    text=False,
                    start_new_session=True,
                )
            finally:
                os.close(slave_fd)
            _set_nonblocking(master_fd)
            pty_master_fd = master_fd
        else:
            proc = subprocess.Popen(
                shell_argv,
                cwd=cwd,
                env=process_env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                start_new_session=True,
            )
            stdin_pipe = proc.stdin
            stdout_pipe = proc.stdout
            stderr_pipe = proc.stderr
            if stdout_pipe is not None:
                _set_nonblocking(stdout_pipe.fileno())
            if stderr_pipe is not None:
                _set_nonblocking(stderr_pipe.fileno())

        record = SessionRecord(
            session_id=session_id,
            agent_id=agent_id,
            command=command,
            cwd=cwd,
            host=host,
            shell_family=shell_family.value,
            use_pty=use_pty,
            timeout_s=int(timeout_s),
            started_at_unix=now,
            deadline_unix=now + float(timeout_s),
            process=proc,
            stdin_pipe=stdin_pipe,
            stdout_pipe=stdout_pipe,
            stderr_pipe=stderr_pipe,
            pty_master_fd=pty_master_fd,
        )

        with self._lock:
            self._sessions[session_id] = record
            self._refresh_locked(record)
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

    def snapshot(self, *, session_id: str, agent_id: str) -> SessionRecord | None:
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None or entry.agent_id != agent_id:
                return None
            self._refresh_locked(entry)
            return entry

    def consume_new_output(
        self, *, session_id: str, agent_id: str
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
        self, *, session_id: str, agent_id: str, payload: bytes
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
                if entry.pty_master_fd is not None:
                    os.write(entry.pty_master_fd, payload)
                elif entry.stdin_pipe is not None:
                    entry.stdin_pipe.write(payload)
                    entry.stdin_pipe.flush()
                else:
                    return False, "session has no writable stdin"
                return True, "input delivered"
            except BrokenPipeError:
                return False, "session stdin is closed"
            except Exception as exc:
                return False, f"failed to write input: {type(exc).__name__}: {exc}"

    def kill(
        self, *, session_id: str, agent_id: str, signal_name: str | None
    ) -> tuple[bool, str, SessionRecord | None]:
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None or entry.agent_id != agent_id:
                return False, "session not found", None
            self._refresh_locked(entry)
            if entry.exit_code is not None:
                return True, "session already exited", entry
            self._terminate_locked(entry, signal_name=signal_name or "TERM")
            time.sleep(0.05)
            self._refresh_locked(entry)
            if entry.exit_code is None:
                self._terminate_locked(entry, signal_name="KILL")
                self._refresh_locked(entry)
            return True, "session terminated", entry

    def clear(self, *, session_id: str, agent_id: str) -> tuple[bool, str]:
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None or entry.agent_id != agent_id:
                return False, "session not found"
            self._refresh_locked(entry)
            if entry.exit_code is None:
                return False, "cannot clear a running session"
            self._close_streams_locked(entry)
            self._sessions.pop(session_id, None)
            return True, "session cleared"

    def list(self, *, agent_id: str, include_exited: bool) -> list[Dict[str, Any]]:
        rows: list[Dict[str, Any]] = []
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
        rows.sort(key=lambda row: float(row["started_at_unix"]), reverse=True)
        return rows

    def _refresh_locked(self, entry: SessionRecord) -> None:
        self._drain_output_locked(entry)
        process = entry.process
        if process is None:
            return

        if entry.exit_code is None and time.time() >= entry.deadline_unix:
            entry.timed_out = True
            entry.killed = True
            self._terminate_locked(entry, signal_name="TERM")
            time.sleep(0.05)
            self._drain_output_locked(entry)
            if process.poll() is None:
                self._terminate_locked(entry, signal_name="KILL")
            self._drain_output_locked(entry)

        exit_code = process.poll()
        if isinstance(exit_code, int):
            entry.exit_code = int(exit_code)
            entry.stopped_at_unix = time.time()
            self._drain_output_locked(entry)
            self._close_streams_locked(entry)

    def _drain_output_locked(self, entry: SessionRecord) -> None:
        if entry.pty_master_fd is not None:
            while True:
                try:
                    chunk = os.read(entry.pty_master_fd, _READ_CHUNK_SIZE)
                except BlockingIOError:
                    break
                except OSError:
                    break
                if not chunk:
                    break
                entry.stdout_buffer.extend(chunk)
            return

        for pipe, target in (
            (entry.stdout_pipe, entry.stdout_buffer),
            (entry.stderr_pipe, entry.stderr_buffer),
        ):
            if pipe is None:
                continue
            fd = pipe.fileno()
            while True:
                try:
                    chunk = os.read(fd, _READ_CHUNK_SIZE)
                except BlockingIOError:
                    break
                except OSError:
                    break
                if not chunk:
                    break
                target.extend(chunk)

    def _terminate_locked(self, entry: SessionRecord, *, signal_name: str) -> None:
        process = entry.process
        if process is None:
            return
        entry.killed = True
        signal_obj = _resolve_signal(signal_name)
        try:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal_obj)
                except Exception:
                    process.send_signal(signal_obj)
            else:
                if signal_obj == signal.SIGKILL:
                    process.kill()
                else:
                    process.terminate()
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _close_streams_locked(self, entry: SessionRecord) -> None:
        for stream in (entry.stdin_pipe, entry.stdout_pipe, entry.stderr_pipe):
            try:
                if stream is not None and not stream.closed:
                    stream.close()
            except Exception:
                continue
        entry.stdin_pipe = None
        entry.stdout_pipe = None
        entry.stderr_pipe = None

        if entry.pty_master_fd is not None:
            try:
                os.close(entry.pty_master_fd)
            except Exception:
                pass
            entry.pty_master_fd = None

        process = entry.process
        if process is not None and process.poll() is not None:
            entry.process = None

    def _status_locked(self, entry: SessionRecord) -> str:
        if entry.exit_code is None:
            return "running"
        if entry.killed or entry.timed_out or entry.exit_code < 0:
            return "killed"
        return "exited"

    def _reset_for_tests(self) -> None:
        with self._lock:
            for entry in list(self._sessions.values()):
                self._terminate_locked(entry, signal_name="KILL")
                self._close_streams_locked(entry)
            self._sessions.clear()


def _resolve_signal(signal_name: str) -> signal.Signals:
    normalized = str(signal_name or "TERM").strip().upper().replace("-", "")
    mapping = {
        "TERM": signal.SIGTERM,
        "SIGTERM": signal.SIGTERM,
        "KILL": signal.SIGKILL,
        "SIGKILL": signal.SIGKILL,
        "INT": signal.SIGINT,
        "SIGINT": signal.SIGINT,
    }
    if normalized in mapping:
        return mapping[normalized]
    try:
        value = getattr(signal, normalized)
        if isinstance(value, signal.Signals):
            return value
        return signal.Signals(int(value))
    except Exception:
        return signal.SIGTERM


PROCESS_MANAGER = ProcessManager()
