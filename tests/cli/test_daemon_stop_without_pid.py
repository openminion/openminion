from __future__ import annotations

from pathlib import Path

from openminion.cli.commands import daemon as daemon_cmd
from openminion.cli.transport.daemon_client import DaemonEndpoint


def _endpoint(tmp_path: Path) -> DaemonEndpoint:
    return DaemonEndpoint(
        config_path=str(tmp_path / "agents.json"),
        host="127.0.0.1",
        port=18789,
        home_root=str(tmp_path),
    )


def _patch_common(monkeypatch, tmp_path, *, pid, reachable):
    endpoint = _endpoint(tmp_path)
    pid_file = tmp_path / "state" / "openminiond.pid"
    monkeypatch.setattr(daemon_cmd, "resolve_daemon_endpoint", lambda *a, **k: endpoint)
    monkeypatch.setattr(daemon_cmd, "load_config", lambda *a, **k: object())
    monkeypatch.setattr(daemon_cmd, "daemon_is_reachable", lambda *a, **k: reachable)
    import openminion.daemon as daemon_mod

    monkeypatch.setattr(daemon_mod, "read_pid", lambda *a, **k: pid)
    monkeypatch.setattr(daemon_mod, "resolve_daemon_pid_file", lambda *a, **k: pid_file)
    return endpoint, pid_file


def test_reachable_endpoint_without_pid_file_explains_and_exits_one(
    monkeypatch, tmp_path, capsys
) -> None:
    endpoint, pid_file = _patch_common(monkeypatch, tmp_path, pid=None, reachable=True)

    def _must_not_signal(*_a, **_k):
        raise AssertionError("daemon stop must not signal when no PID is known")

    import psutil

    monkeypatch.setattr(psutil, "Process", _must_not_signal)

    rc = daemon_cmd.daemon_stop(None)

    out = capsys.readouterr().out
    assert rc == 1
    assert f"{endpoint.host}:{endpoint.port}" in out
    assert str(pid_file) in out
    assert "No process was signalled" in out
    assert "daemon status --json" in out


def test_unreachable_endpoint_reports_not_running(
    monkeypatch, tmp_path, capsys
) -> None:
    _patch_common(monkeypatch, tmp_path, pid=None, reachable=False)

    rc = daemon_cmd.daemon_stop(None)

    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == "Daemon is not running."


def test_stale_pid_file_is_removed_without_signalling(
    monkeypatch, tmp_path, capsys
) -> None:
    _, pid_file = _patch_common(monkeypatch, tmp_path, pid=4242, reachable=False)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text("4242\n", encoding="utf-8")

    import openminion.daemon as daemon_mod

    monkeypatch.setattr(daemon_mod, "process_alive", lambda _pid: False)

    def _must_not_signal(*_a, **_k):
        raise AssertionError("daemon stop must not signal a dead PID")

    import psutil

    monkeypatch.setattr(psutil, "Process", _must_not_signal)

    rc = daemon_cmd.daemon_stop(None)

    assert rc == 0
    assert "Removed stale daemon PID file." in capsys.readouterr().out
    assert not pid_file.exists()
