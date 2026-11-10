"""`daemon start` must report an exited child distinctly from a live timeout."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import openminion.daemon as daemon_mod
from openminion.cli.commands import daemon as daemon_cmd
from openminion.cli.transport.daemon_client import DaemonEndpoint


class _FakeProcess:
    def __init__(self, *, returncode: int | None, pid: int = 4242) -> None:
        self.returncode = returncode
        self.pid = pid
        self.terminated = False
        self.killed = False
        self.waited = 0

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        self.waited += 1
        return self.returncode or 0

    def kill(self) -> None:
        self.killed = True


@pytest.fixture()
def daemon_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pid_file = tmp_path / "state" / "openminiond.pid"
    log_file = tmp_path / "logs" / "daemon.log"
    monkeypatch.setattr(daemon_cmd, "load_config", lambda path: SimpleNamespace())
    monkeypatch.setattr(daemon_mod, "process_alive", lambda pid: False)
    monkeypatch.setattr(daemon_mod, "resolve_daemon_pid_file", lambda *a, **k: pid_file)
    monkeypatch.setattr(daemon_mod, "resolve_daemon_log_file", lambda *a, **k: log_file)
    monkeypatch.setattr(
        daemon_cmd, "probe_daemon_endpoint", lambda *a, **k: ("unreachable", {})
    )
    monkeypatch.setattr(daemon_cmd.time, "sleep", lambda seconds: None)
    return pid_file, log_file


def _message(result: dict[str, object]) -> str:
    return str(result.get("message", ""))


def _endpoint() -> DaemonEndpoint:
    return DaemonEndpoint(config_path="/tmp/agents.json", host="127.0.0.1", port=18789)


def test_early_nonzero_exit_reports_the_observed_code(
    daemon_paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pid_file, log_file = daemon_paths
    log_file.parent.mkdir(parents=True)
    log_file.write_text("boom\n", encoding="utf-8")
    process = _FakeProcess(returncode=3)
    monkeypatch.setattr(daemon_cmd, "_spawn_daemon", lambda *a, **k: process)

    result = daemon_cmd._start_daemon(_endpoint())

    assert result["ok"] is False
    assert "exited with code 3 before answering on 127.0.0.1:18789" in _message(result)
    assert f"Inspect logs at {log_file}." in _message(result)
    assert process.terminated is False


def test_early_zero_exit_is_still_an_exit_not_a_timeout(
    daemon_paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pid_file, log_file = daemon_paths
    log_file.parent.mkdir(parents=True)
    log_file.write_text("", encoding="utf-8")
    process = _FakeProcess(returncode=0)
    monkeypatch.setattr(daemon_cmd, "_spawn_daemon", lambda *a, **k: process)

    result = daemon_cmd._start_daemon(_endpoint())

    assert "exited with code 0 before answering" in _message(result)
    assert "failed to become healthy" not in _message(result)


def test_early_exit_without_a_log_file_says_so(
    daemon_paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pid_file, log_file = daemon_paths
    process = _FakeProcess(returncode=1)
    monkeypatch.setattr(daemon_cmd, "_spawn_daemon", lambda *a, **k: process)

    result = daemon_cmd._start_daemon(_endpoint())

    assert "No daemon log file was written." in _message(result)
    assert str(log_file) not in _message(result)


def test_live_child_at_the_deadline_keeps_the_timeout_message(
    daemon_paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pid_file, log_file = daemon_paths
    process = _FakeProcess(returncode=None)
    monkeypatch.setattr(daemon_cmd, "_spawn_daemon", lambda *a, **k: process)
    clock = iter(range(0, 100))
    monkeypatch.setattr(daemon_cmd.time, "time", lambda: next(clock))

    result = daemon_cmd._start_daemon(_endpoint())

    assert "failed to become healthy within timeout" in _message(result)
    assert "exited with code" not in _message(result)
    assert process.terminated is True
    assert process.waited == 1


def test_endpoint_mismatch_branch_is_unchanged(
    daemon_paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pid_file, _log_file = daemon_paths
    process = _FakeProcess(returncode=None)
    monkeypatch.setattr(daemon_cmd, "_spawn_daemon", lambda *a, **k: process)
    monkeypatch.setattr(
        daemon_cmd,
        "probe_daemon_endpoint",
        lambda *a, **k: (
            daemon_cmd._PROBE_STATUS_MISMATCH,
            {"daemon": {"config_path": "/tmp/other.json"}},
        ),
    )

    result = daemon_cmd._start_daemon(_endpoint())

    assert result["ok"] is False
    assert "endpoint identity does not match" in _message(result)
    assert "/tmp/other.json" in _message(result)


def test_live_pid_file_with_unreachable_daemon_is_unchanged(
    daemon_paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file, _log_file = daemon_paths
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text("5150\n", encoding="utf-8")
    monkeypatch.setattr(daemon_mod, "process_alive", lambda pid: True)
    monkeypatch.setattr(
        daemon_cmd,
        "_spawn_daemon",
        lambda *a, **k: pytest.fail("must not spawn while a live PID is recorded"),
    )

    result = daemon_cmd._start_daemon(_endpoint())

    assert result["ok"] is False
    assert "PID file exists for running process 5150" in _message(result)
    # Refusing must not consume the live daemon's PID file.
    assert pid_file.read_text(encoding="utf-8").strip() == "5150"


def test_child_exiting_during_the_final_sleep_is_reported_as_an_exit(
    daemon_paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last probe can see a live child that exits before the deadline expires."""
    _pid_file, log_file = daemon_paths
    log_file.parent.mkdir(parents=True)
    log_file.write_text("boom\n", encoding="utf-8")
    state = {"exited": False}

    class _Proc:
        pid = 4242
        returncode = 3
        terminated = False

        def poll(self):
            return 3 if state["exited"] else None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    process = _Proc()
    monkeypatch.setattr(daemon_cmd, "_spawn_daemon", lambda *a, **k: process)
    clock = iter([0.0, 9.95, 10.05])
    monkeypatch.setattr(daemon_cmd.time, "time", lambda: next(clock))
    monkeypatch.setattr(
        daemon_cmd.time, "sleep", lambda seconds: state.update(exited=True)
    )

    result = daemon_cmd._start_daemon(_endpoint())

    message = _message(result)
    assert "exited with code 3 before answering" in message
    assert "failed to become healthy" not in message
    assert process.terminated is False
