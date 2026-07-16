from __future__ import annotations

from dataclasses import dataclass, field
import fcntl
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import subprocess
import termios
import time
from typing import Callable

import pyte


@dataclass(slots=True)
class _ForkProcess:
    pid: int
    returncode: int | None = None

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        waited_pid, status = os.waitpid(self.pid, os.WNOHANG)
        if waited_pid == 0:
            return None
        self.returncode = os.waitstatus_to_exitcode(status)
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(str(self.pid), timeout)
            time.sleep(0.02)
        return int(self.returncode or 0)


@dataclass(slots=True)
class PtySession:
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)
    rows: int = 42
    cols: int = 140
    on_transcript_update: Callable[[str], None] | None = None

    _master_fd: int | None = field(default=None, init=False)
    _process: _ForkProcess | None = field(default=None, init=False)
    _transcript: str = field(default="", init=False)
    _screen: pyte.Screen = field(init=False)
    _stream: pyte.Stream = field(init=False)
    _screen_history: str = field(default="", init=False)
    _last_screen: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self._screen = pyte.Screen(self.cols, self.rows)
        self._stream = pyte.Stream(self._screen)

    def __enter__(self) -> PtySession:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.terminate()

    @property
    def transcript(self) -> str:
        self._read_available(timeout=0.05)
        return self._transcript

    @property
    def screen_text(self) -> str:
        self._read_available(timeout=0.05)
        return self._last_screen

    @property
    def visible_transcript(self) -> str:
        self._read_available(timeout=0.05)
        return self._screen_history

    def start(self) -> None:
        if os.name != "posix":
            raise RuntimeError("PTY focus E2E tests require a POSIX platform")
        env = os.environ.copy()
        env.update(self.env)
        env.setdefault("TERM", "xterm-256color")
        pid, master_fd = pty.fork()
        if pid == 0:
            try:
                os.chdir(self.cwd)
                os.execvpe(self.argv[0], self.argv, env)
            except BaseException:
                os._exit(127)
        self._process = _ForkProcess(pid)
        self._master_fd = master_fd
        self._set_window_size(master_fd)
        self._set_nonblocking(master_fd)

    def send(self, text: str) -> None:
        if self._master_fd is None:
            raise RuntimeError("PTY session is not running")
        data = memoryview(text.encode("utf-8"))
        while data:
            try:
                written = os.write(self._master_fd, data)
            except BlockingIOError:
                select.select([], [self._master_fd], [], 0.05)
                continue
            if written <= 0:
                raise RuntimeError("PTY write failed")
            data = data[written:]

    def type_line(self, text: str) -> None:
        self.send(text)
        time.sleep(0.05)
        self.send("\r")

    def wait_for_visible_match_after(
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
            match = compiled.search(self._screen_history[offset:])
            if match is not None:
                return match
            if self._process is not None and self._process.poll() is not None:
                break
        raise AssertionError(
            f"timed out waiting for visible {compiled.pattern!r}\n"
            f"{self._screen_history[-2000:]}"
        )

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
            decoded = chunk.decode("utf-8", errors="replace")
            self._transcript += decoded
            self._stream.feed(decoded)
            self._capture_screen()
            if self.on_transcript_update is not None:
                self.on_transcript_update(self._transcript)
            if time.monotonic() >= end:
                return

    def _set_window_size(self, fd: int) -> None:
        size = struct.pack("HHHH", self.rows, self.cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, size)

    def _capture_screen(self) -> None:
        lines = self._screen_display_lines()
        while lines and not lines[-1]:
            lines.pop()
        snapshot = "\n".join(lines)
        if not snapshot or snapshot == self._last_screen:
            return
        self._last_screen = snapshot
        # Keep screen redraws distinguishable from ordinary transcript spacing.
        separator = "\n\f\n" if self._screen_history else ""
        self._screen_history += separator + snapshot

    def _screen_display_lines(self) -> list[str]:
        lines: list[str] = []
        for y in range(self._screen.lines):
            line = self._screen.buffer[y]
            text = "".join(
                line[x].data for x in range(self._screen.columns) if line[x].data
            )
            lines.append(text.rstrip())
        return lines

    @staticmethod
    def _set_nonblocking(fd: int) -> None:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
