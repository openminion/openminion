from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

import openminion.daemon as daemon_core
from openminion.cli.commands import daemon as daemon_command
from openminion.cli.transport.daemon_client import DaemonEndpoint


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
    monkeypatch.setattr(daemon_core, "resolve_daemon_pid_file", lambda _cfg: pid_file)
    monkeypatch.setattr(daemon_core, "read_pid", lambda _path: 4321)
    monkeypatch.setattr(daemon_core, "process_alive", lambda _pid: True)
    monkeypatch.setattr(
        daemon_command,
        "probe_daemon_endpoint",
        lambda _endpoint: ("ok", {"daemon": {"config_path": "/tmp/openminion.json"}}),
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
    monkeypatch.setattr(daemon_command, "load_config", lambda _cfg: config)
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


def test_desktop_bootstrap_writes_one_validated_private_record(
    monkeypatch, capsys
) -> None:
    endpoint = DaemonEndpoint(
        config_path="/tmp/home/config.json",
        host="127.0.0.1",
        port=4100,
        token="master-token",
        home_root="/tmp/home",
        data_root="/tmp/home/data",
    )
    config_id = daemon_command.build_config_id(
        endpoint.config_path,
        endpoint.home_root,
        endpoint.data_root,
    )
    response = {
        "ok": True,
        "lease": {
            "client_id": "client-1",
            "client_token": "x" * 43,
            "protocol": 1,
            "issued_at": "2026-08-16T00:00:00Z",
            "expires_at": "2999-08-16T12:00:00Z",
            "config_id": config_id,
            "capabilities": ["daemon.health"],
        },
        "meta": {
            "request_id": "request-1",
            "method": "POST",
            "path": "/v1/client/leases",
        },
    }
    capabilities = {
        "ok": True,
        "daemon_version": daemon_command._package_version(),
        "config_id": config_id,
        "protocol": 1,
    }
    replies = iter(((200, response), (200, capabilities)))
    captured: list[tuple[int, dict[str, object]]] = []
    monkeypatch.setattr(daemon_command, "resolve_daemon_endpoint", lambda *_a, **_k: endpoint)
    monkeypatch.setattr(daemon_command, "ensure_daemon_running", lambda *_a, **_k: endpoint)
    monkeypatch.setattr(daemon_command, "daemon_request", lambda **_k: next(replies))
    monkeypatch.setattr(daemon_command.os, "fstat", lambda _fd: object())
    monkeypatch.setattr(
        daemon_command,
        "_write_readiness_record",
        lambda fd, record: captured.append((fd, record)),
    )

    assert daemon_command.daemon_desktop_bootstrap("config.json", fd=3) == 0
    assert len(captured) == 1
    assert captured[0][0] == 3
    assert captured[0][1]["event"] == "desktop.client.ready"
    assert captured[0][1]["config_id"] == config_id
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""


def test_desktop_bootstrap_rejects_blank_token_without_start(monkeypatch, capsys) -> None:
    endpoint = DaemonEndpoint(
        config_path="/tmp/config.json",
        host="127.0.0.1",
        port=4100,
        token="",
    )
    monkeypatch.setattr(daemon_command, "resolve_daemon_endpoint", lambda *_a, **_k: endpoint)
    started = False

    def _unexpected_start(*_args, **_kwargs):
        nonlocal started
        started = True

    monkeypatch.setattr(daemon_command, "ensure_daemon_running", _unexpected_start)

    assert daemon_command.daemon_desktop_bootstrap("config.json", fd=3) == 1
    assert started is False
    assert capsys.readouterr().err.strip() == (
        "desktop bootstrap failed: desktop_ipc_token_required"
    )


def test_desktop_bootstrap_rejects_bad_fd(monkeypatch, capsys) -> None:
    assert daemon_command.daemon_desktop_bootstrap("config.json", fd=4) == 1
    assert capsys.readouterr().err.strip() == (
        "desktop bootstrap failed: invalid_readiness_fd"
    )
