from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

import openminion.daemon as daemon_core
from openminion.base.config import ConfigManager
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
    monkeypatch.setattr(
        ConfigManager,
        "load",
        lambda *_args, **_kwargs: SimpleNamespace(
            base_config=config,
            data_root=Path("/tmp/data"),
        ),
    )
    monkeypatch.setattr(daemon_core, "resolve_daemon_pid_file", lambda _cfg: pid_file)
    monkeypatch.setattr(daemon_core, "read_pid", lambda _path: 4321)
    monkeypatch.setattr(daemon_core, "process_alive", lambda _pid: True)
    monkeypatch.setattr(
        daemon_command,
        "probe_daemon_endpoint",
        lambda _endpoint: (
            "ok",
            {
                "daemon": {
                    "config_path": "/tmp/openminion.json",
                    "data_root": "/tmp/data",
                },
                "normalized_health_snapshot": {"components": []},
            },
        ),
    )
    monkeypatch.setattr(daemon_core, "resolve_daemon_log_file", lambda _cfg: log_file)

    code = daemon_command.daemon_status("config.json")

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "config_path": "/tmp/openminion.json",
        "endpoint_status": "ok",
        "host": "127.0.0.1",
        "lifecycle": "running",
        "log_file": str(log_file),
        "ok": True,
        "pid": 4321,
        "pid_alive": True,
        "pid_file": str(pid_file),
        "port": 4100,
        "reachable": True,
        "remote_config_path": "/tmp/openminion.json",
        "remote_data_root": "/tmp/data",
        "scheduler": {
            "check_command": "openminion service status cron",
            "hosted_by": "daemon",
            "reason": "scheduler_not_attached",
            "state": "degraded",
        },
        "status": "running",
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
    monkeypatch.setattr(
        ConfigManager,
        "load",
        lambda *_args, **_kwargs: SimpleNamespace(
            base_config=config,
            data_root=Path("/tmp/data"),
        ),
    )
    monkeypatch.setattr(daemon_core, "resolve_daemon_pid_file", lambda _cfg: pid_file)
    monkeypatch.setattr(daemon_core, "read_pid", lambda _path: None)
    monkeypatch.setattr(daemon_core, "process_alive", lambda _pid: False)
    monkeypatch.setattr(
        daemon_command,
        "probe_daemon_endpoint",
        lambda _endpoint: ("unreachable", {}),
    )
    monkeypatch.setattr(daemon_core, "resolve_daemon_log_file", lambda _cfg: log_file)

    code = daemon_command.daemon_status("config.json")

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["reachable"] is False
    assert payload["endpoint_status"] == "unreachable"
    assert payload["pid"] is None
    assert payload["pid_alive"] is False
