from __future__ import annotations

from dataclasses import dataclass, field
import fcntl
import os
from pathlib import Path
import re
import select
import signal
import struct
import subprocess
import termios
import time


@dataclass(slots=True)
class PtySession:
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)
    rows: int = 42
    cols: int = 140

    _master_fd: int | None = field(default=None, init=False)
    _process: subprocess.Popen[bytes] | None = field(default=None, init=False)
    _transcript: str = field(default="", init=False)

    def __enter__(self) -> PtySession:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.terminate()

    @property
    def transcript(self) -> str:
        self._read_available(timeout=0.05)
        return self._transcript

    def start(self) -> None:
        if os.name != "posix":
            raise RuntimeError("PTY focus E2E tests require a POSIX platform")
        master_fd, slave_fd = os.openpty()
        self._set_window_size(slave_fd)
        env = os.environ.copy()
        env.update(self.env)
        env.setdefault("TERM", "xterm-256color")
        self._process = subprocess.Popen(
            self.argv,
            cwd=str(self.cwd),
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            preexec_fn=os.setsid,
        )
        os.close(slave_fd)
        self._master_fd = master_fd
        self._set_nonblocking(master_fd)

    def send(self, text: str) -> None:
        if self._master_fd is None:
            raise RuntimeError("PTY session is not running")
        os.write(self._master_fd, text.encode("utf-8"))

    def type_line(self, text: str) -> None:
        self.send(f"{text}\n")

    def wait_for(self, pattern: str | re.Pattern[str], *, timeout: float) -> str:
        return self.wait_for_after(pattern, offset=0, timeout=timeout)

    def wait_for_after(
        self,
        pattern: str | re.Pattern[str],
        *,
        offset: int,
        timeout: float,
    ) -> str:
        return self.wait_for_match_after(pattern, offset=offset, timeout=timeout).string

    def wait_for_match_after(
        self,
        pattern: str | re.Pattern[str],
        *,
        offset: int,
        timeout: float,
    ) -> re.Match[str]:
        compiled = re.compile(pattern) if isinstance(pattern, str) else pattern
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._read_available(timeout=0.1)
            match = compiled.search(self._transcript[offset:])
            if match is not None:
                return match
            if self._process is not None and self._process.poll() is not None:
                self._read_available(timeout=0.1)
                match = compiled.search(self._transcript[offset:])
                if match is not None:
                    return match
                raise AssertionError(
                    f"process exited before pattern {compiled.pattern!r}\n"
                    f"{self._transcript[-2000:]}"
                )
        raise AssertionError(
            f"timed out waiting for {compiled.pattern!r}\n{self._transcript[-2000:]}"
        )

    def terminate(self) -> None:
        process = self._process
        master_fd = self._master_fd
        if process is not None and process.poll() is None:
            try:
                self.type_line("/exit")
                process.wait(timeout=3)
            except Exception:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=3)
                except Exception:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except Exception:
                        pass
        if master_fd is not None:
            self._read_available(timeout=0.05)
            try:
                os.close(master_fd)
            except OSError:
                pass
        self._master_fd = None
        self._process = None

    def _read_available(self, *, timeout: float) -> None:
        if self._master_fd is None:
            return
        end = time.monotonic() + timeout
        while True:
            wait = max(0.0, min(0.05, end - time.monotonic()))
            readable, _, _ = select.select([self._master_fd], [], [], wait)
            if not readable:
                return
            try:
                chunk = os.read(self._master_fd, 65536)
            except BlockingIOError:
                return
            except OSError:
                return
            if not chunk:
                return
            self._transcript += chunk.decode("utf-8", errors="replace")
            if time.monotonic() >= end:
                return

    def _set_window_size(self, fd: int) -> None:
        size = struct.pack("HHHH", self.rows, self.cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, size)

    @staticmethod
    def _set_nonblocking(fd: int) -> None:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
