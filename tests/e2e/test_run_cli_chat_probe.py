from __future__ import annotations

import os
import sys
import textwrap

from tests.e2e.runners import run_cli_chat_probe as probe_runner
from tests.e2e.runners.run_cli_chat_probe import _open_probe_pty, _run_probe_session

import pytest

pytestmark = pytest.mark.e2e


def _helper_command(source: str) -> list[str]:
    return [sys.executable, "-u", "-c", textwrap.dedent(source)]


def test_probe_waits_for_real_prompt_boundary_after_echoed_input() -> None:
    command = _helper_command(
        """
        import sys

        PREFIX = "[probe|agent]"

        def write(text: str) -> None:
            sys.stdout.write(text)
            sys.stdout.flush()

        write("chat ready\\n")
        write(f"{PREFIX} you> ")
        for raw in sys.stdin:
            message = raw.rstrip("\\n")
            if message == "/exit":
                break
            write(f"{PREFIX} you> {message}\\n")
            write(f"{PREFIX} agent: handled {message}\\n")
            write(f"{PREFIX} you> ")
        """
    )

    exit_code, transcript = _run_probe_session(
        cmd=command,
        env=dict(os.environ),
        cwd=os.getcwd(),
        messages=["alpha", "beta"],
        timeout_seconds=1.5,
    )

    assert exit_code == 0
    assert "[probe-status]" not in transcript
    assert "[probe|agent] agent: handled alpha" in transcript
    assert "[probe|agent] agent: handled beta" in transcript
    assert transcript.count("[probe|agent] you> ") >= 3


def test_probe_timeout_preserves_full_transcript_and_classifies_turn_timeout() -> None:
    command = _helper_command(
        """
        import sys
        import time

        PREFIX = "[probe|agent]"

        def write(text: str) -> None:
            sys.stdout.write(text)
            sys.stdout.flush()

        write("chat ready\\n")
        write(f"{PREFIX} you> ")
        for raw in sys.stdin:
            message = raw.rstrip("\\n")
            if message == "/exit":
                break
            write(f"{PREFIX} you> {message}\\n")
            write(f"{PREFIX} agent: partial response")
            time.sleep(1.0)
        """
    )

    exit_code, transcript = _run_probe_session(
        cmd=command,
        env=dict(os.environ),
        cwd=os.getcwd(),
        messages=["stall-turn"],
        timeout_seconds=0.2,
    )

    assert exit_code == 124
    assert "chat ready" in transcript
    assert "[probe|agent] you> stall-turn" in transcript
    assert "partial response" in transcript
    assert "[probe-status] phase=turn_timeout exit_code=124" in transcript


def test_probe_classifies_startup_timeout() -> None:
    command = _helper_command(
        """
        import sys
        import time

        sys.stdout.write("booting without prompt")
        sys.stdout.flush()
        time.sleep(1.0)
        """
    )

    exit_code, transcript = _run_probe_session(
        cmd=command,
        env=dict(os.environ),
        cwd=os.getcwd(),
        messages=[],
        timeout_seconds=0.2,
    )

    assert exit_code == 124
    assert "booting without prompt" in transcript
    assert "[probe-status] phase=startup_timeout exit_code=124" in transcript


def test_probe_classifies_child_nonzero_exit() -> None:
    command = _helper_command(
        """
        import sys

        sys.stdout.write("[probe|agent] you> ")
        sys.stdout.flush()
        raise SystemExit(7)
        """
    )

    exit_code, transcript = _run_probe_session(
        cmd=command,
        env=dict(os.environ),
        cwd=os.getcwd(),
        messages=[],
        timeout_seconds=0.5,
    )

    assert exit_code == 7
    assert "[probe|agent] you> " in transcript
    assert "[probe-status] phase=child_nonzero_exit exit_code=7" in transcript


def test_probe_dump_debug_on_exit_captures_debug_block_before_clean_exit() -> None:
    command = _helper_command(
        """
        import json
        import sys

        PREFIX = "[probe|agent]"

        def write(text: str) -> None:
            sys.stdout.write(text)
            sys.stdout.flush()

        write("chat ready\\n")
        write(f"{PREFIX} you> ")
        for raw in sys.stdin:
            message = raw.rstrip("\\n")
            if message == "/debug":
                write(
                    json.dumps(
                        {
                            "last_turn": {
                                "metadata": {
                                    "brain_status": "completed",
                                    "tool_calls_count": 2,
                                }
                            }
                        }
                    )
                )
                write("\\n")
                write(f"{PREFIX} you> ")
                continue
            if message == "/exit":
                break
            write(f"{PREFIX} agent: handled {message}\\n")
            write(f"{PREFIX} you> ")
        """
    )

    exit_code, transcript = _run_probe_session(
        cmd=command,
        env=dict(os.environ),
        cwd=os.getcwd(),
        messages=["alpha"],
        timeout_seconds=1.5,
        dump_debug_on_exit=True,
    )

    assert exit_code == 0
    assert '"last_turn"' in transcript
    assert '"tool_calls_count": 2' in transcript
    assert "[probe-status]" not in transcript


def test_probe_auto_confirm_replies_yes_to_confirmation_prompts() -> None:
    command = _helper_command(
        """
        import sys

        PREFIX = "[probe|agent]"

        def write(text: str) -> None:
            sys.stdout.write(text)
            sys.stdout.flush()

        awaiting_confirmation = False
        write("chat ready\\n")
        write(f"{PREFIX} you> ")
        for raw in sys.stdin:
            message = raw.rstrip("\\n")
            if message == "/exit":
                break
            if not awaiting_confirmation:
                write("Policy confirmation required.\\n")
                write("Reply exactly yes to confirm or exactly no to cancel.\\n")
                write(f"{PREFIX} you> ")
                awaiting_confirmation = True
                continue
            write(f"{PREFIX} agent: confirmed {message}\\n")
            write(f"{PREFIX} you> ")
        """
    )

    exit_code, transcript = _run_probe_session(
        cmd=command,
        env=dict(os.environ),
        cwd=os.getcwd(),
        messages=["write scratch file"],
        timeout_seconds=1.5,
        auto_confirm=True,
    )

    assert exit_code == 0
    assert "Policy confirmation required." in transcript
    assert "[probe|agent] agent: confirmed yes" in transcript
    assert "[probe-status]" not in transcript


def test_probe_auto_confirm_limit_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENMINION_LIVE_CLI_CHAT_AUTO_CONFIRM_LIMIT", raising=False)
    assert probe_runner._auto_confirm_limit() == 32

    monkeypatch.setenv("OPENMINION_LIVE_CLI_CHAT_AUTO_CONFIRM_LIMIT", "12")
    assert probe_runner._auto_confirm_limit() == 12

    monkeypatch.setenv("OPENMINION_LIVE_CLI_CHAT_AUTO_CONFIRM_LIMIT", "not-an-int")
    assert probe_runner._auto_confirm_limit() == 32


def test_probe_drains_large_shutdown_output_before_waiting_for_exit() -> None:
    command = _helper_command(
        """
        import sys

        PREFIX = "[probe|agent]"

        def write(text: str) -> None:
            sys.stdout.write(text)
            sys.stdout.flush()

        write("chat ready\\n")
        write(f"{PREFIX} you> ")
        for raw in sys.stdin:
            message = raw.rstrip("\\n")
            if message == "/exit":
                write("shutdown-start\\n")
                write("x" * 131072)
                write("\\nshutdown-end\\n")
                break
            write(f"{PREFIX} agent: handled {message}\\n")
            write(f"{PREFIX} you> ")
        """
    )

    exit_code, transcript = _run_probe_session(
        cmd=command,
        env=dict(os.environ),
        cwd=os.getcwd(),
        messages=["alpha"],
        timeout_seconds=1.5,
    )

    assert exit_code == 0
    assert "shutdown-start" in transcript
    assert "shutdown-end" in transcript
    assert "[probe-status]" not in transcript


def test_probe_handles_partial_writes_for_long_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = _helper_command(
        """
        import sys

        PREFIX = "[probe|agent]"

        def write(text: str) -> None:
            sys.stdout.write(text)
            sys.stdout.flush()

        write("chat ready\\n")
        write(f"{PREFIX} you> ")
        for raw in sys.stdin:
            message = raw.rstrip("\\n")
            if message == "/exit":
                break
            write(f"{PREFIX} agent: handled {len(message)} chars\\n")
            write(f"{PREFIX} you> ")
        """
    )

    real_write = probe_runner.os.write
    long_message = "x" * 32768

    def partial_write(fd: int, data: bytes | memoryview) -> int:
        if fd < 0:
            return real_write(fd, data)
        raw = bytes(data)
        if len(raw) <= 64:
            return real_write(fd, raw)
        return real_write(fd, raw[:64])

    monkeypatch.setattr(probe_runner.os, "write", partial_write)

    exit_code, transcript = _run_probe_session(
        cmd=command,
        env=dict(os.environ),
        cwd=os.getcwd(),
        messages=[long_message],
        timeout_seconds=5.0,
    )

    assert exit_code == 0
    assert f"[probe|agent] agent: handled {len(long_message)} chars" in transcript
    assert "[probe-status]" not in transcript


def test_probe_write_drains_pending_child_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes = {"count": 0}
    transcript: list[str] = []
    reads = [b"pending-output-start\n", b"pending-output-end\n"]

    def temporarily_blocking_write(fd: int, data: bytes | memoryview) -> int:
        del fd
        writes["count"] += 1
        if writes["count"] == 1:
            raise BlockingIOError()
        return len(data)

    def fake_select(read_fds, write_fds, error_fds, timeout):
        del error_fds, timeout
        if read_fds and reads:
            return read_fds, [], []
        return [], write_fds, []

    def fake_read(fd: int, size: int) -> bytes:
        del fd, size
        if reads:
            return reads.pop(0)
        raise BlockingIOError()

    monkeypatch.setattr(probe_runner.os, "write", temporarily_blocking_write)
    monkeypatch.setattr(probe_runner.os, "read", fake_read)
    monkeypatch.setattr(probe_runner.select, "select", fake_select)

    probe_runner._write_all(
        master_fd=123,
        payload=b"alpha\n",
        timeout_seconds=1.0,
        phase="input_write_timeout",
        transcript=transcript,
    )

    assert writes["count"] == 2
    output = "".join(transcript)
    assert "pending-output-start" in output
    assert "pending-output-end" in output


def test_open_probe_pty_prefers_os_openpty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.openpty", lambda: (11, 12))
    assert _open_probe_pty() == (11, 12)


def test_probe_dump_debug_on_exit_captures_debug_block_before_shutdown_timeout() -> (
    None
):
    command = _helper_command(
        """
        import json
        import sys
        import time

        PREFIX = "[probe|agent]"

        def write(text: str) -> None:
            sys.stdout.write(text)
            sys.stdout.flush()

        write("chat ready\\n")
        write(f"{PREFIX} you> ")
        for raw in sys.stdin:
            message = raw.rstrip("\\n")
            if message == "/debug":
                write(
                    json.dumps(
                        {
                            "last_turn": {
                                "metadata": {
                                    "brain_status": "waiting_user",
                                    "tool_calls_count": 1,
                                }
                            }
                        }
                    )
                )
                write("\\n")
                write(f"{PREFIX} you> ")
                continue
            if message == "/exit":
                time.sleep(10.0)
                break
            write(f"{PREFIX} agent: handled {message}\\n")
            write(f"{PREFIX} you> ")
        """
    )

    exit_code, transcript = _run_probe_session(
        cmd=command,
        env=dict(os.environ),
        cwd=os.getcwd(),
        messages=["alpha"],
        timeout_seconds=1.0,
        dump_debug_on_exit=True,
    )

    assert exit_code in {0, 124}
    assert '"last_turn"' in transcript
    assert '"tool_calls_count": 1' in transcript
    if exit_code == 124:
        assert "[probe-status] phase=shutdown_timeout exit_code=124" in transcript
    else:
        assert "[probe-status]" not in transcript
