from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

from openminion.cli.commands import daemon as daemon_command


def test_daemon_status_json_output_when_reachable(monkeypatch, capsys) -> None:
    endpoint = SimpleNamespace(
        host="127.0.0.1",
        port=4100,
        config_path="/tmp/openminion.json",
    )
    config = SimpleNamespace()
    pid_file = Path("/tmp/openminion.pid")
    log_file = Path("/tmp/openminion.log")

    monkeypatch.setattr(
        daemon_command, "resolve_daemon_endpoint", lambda _cfg: endpoint
    )
    monkeypatch.setattr(daemon_command, "load_config", lambda _cfg: config)
    monkeypatch.setattr(
        daemon_command, "resolve_daemon_pid_file", lambda _cfg: pid_file
    )
    monkeypatch.setattr(daemon_command, "read_pid", lambda _path: 4321)
    monkeypatch.setattr(daemon_command, "process_alive", lambda _pid: True)
    monkeypatch.setattr(
        daemon_command,
        "probe_daemon_endpoint",
        lambda _endpoint: ("ok", {"daemon": {"config_path": "/tmp/openminion.json"}}),
    )
    monkeypatch.setattr(
        daemon_command, "resolve_daemon_log_file", lambda _cfg: log_file
    )

    code = daemon_command.daemon_status("config.json")

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "endpoint_status": "ok",
        "host": "127.0.0.1",
        "log_file": str(log_file),
        "ok": True,
        "pid": 4321,
        "pid_alive": True,
        "pid_file": str(pid_file),
        "port": 4100,
        "reachable": True,
        "remote_config_path": "/tmp/openminion.json",
    }


def test_daemon_status_json_output_when_unreachable(monkeypatch, capsys) -> None:
    endpoint = SimpleNamespace(
        host="127.0.0.1",
        port=4100,
        config_path="/tmp/openminion.json",
    )
    config = SimpleNamespace()
    pid_file = Path("/tmp/openminion.pid")
    log_file = Path("/tmp/openminion.log")

    monkeypatch.setattr(
        daemon_command, "resolve_daemon_endpoint", lambda _cfg: endpoint
    )
    monkeypatch.setattr(daemon_command, "load_config", lambda _cfg: config)
    monkeypatch.setattr(
        daemon_command, "resolve_daemon_pid_file", lambda _cfg: pid_file
    )
    monkeypatch.setattr(daemon_command, "read_pid", lambda _path: None)
    monkeypatch.setattr(daemon_command, "process_alive", lambda _pid: False)
    monkeypatch.setattr(
        daemon_command,
        "probe_daemon_endpoint",
        lambda _endpoint: ("unreachable", {}),
    )
    monkeypatch.setattr(
        daemon_command, "resolve_daemon_log_file", lambda _cfg: log_file
    )

    code = daemon_command.daemon_status("config.json")

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["reachable"] is False
    assert payload["endpoint_status"] == "unreachable"
    assert payload["pid"] is None
    assert payload["pid_alive"] is False
