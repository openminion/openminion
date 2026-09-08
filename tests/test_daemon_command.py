from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

import pytest

import openminion.daemon as daemon_core
from openminion.base.config import ConfigManager
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
    root_calls: list[tuple[str, object, object]] = []

    def _resolve(_config, *, home_root=None, data_root=None):
        root_calls.append(("resolve", home_root, data_root))
        return endpoint

    def _ensure(_config, *, auto_start, home_root=None, data_root=None):
        assert auto_start is True
        root_calls.append(("ensure", home_root, data_root))
        return endpoint

    monkeypatch.setattr(daemon_command, "resolve_daemon_endpoint", _resolve)
    monkeypatch.setattr(daemon_command, "ensure_daemon_running", _ensure)
    monkeypatch.setattr(daemon_command, "daemon_request", lambda **_k: next(replies))
    monkeypatch.setattr(daemon_command.os, "fstat", lambda _fd: object())
    monkeypatch.setattr(
        daemon_command,
        "_write_readiness_record",
        lambda fd, record: captured.append((fd, record)),
    )

    assert (
        daemon_command.daemon_desktop_bootstrap(
            "config.json",
            fd=3,
            home_root="/tmp/home",
            data_root="/tmp/home/data",
        )
        == 0
    )
    assert len(captured) == 1
    assert captured[0][0] == 3
    assert captured[0][1]["event"] == "desktop.client.ready"
    assert captured[0][1]["config_id"] == config_id
    assert root_calls == [
        ("resolve", "/tmp/home", "/tmp/home/data"),
        ("ensure", "/tmp/home", "/tmp/home/data"),
    ]
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""


def test_desktop_bootstrap_rejects_blank_token_without_start(
    monkeypatch, capsys
) -> None:
    endpoint = DaemonEndpoint(
        config_path="/tmp/config.json",
        host="127.0.0.1",
        port=4100,
        token="",
    )
    monkeypatch.setattr(
        daemon_command, "resolve_daemon_endpoint", lambda *_a, **_k: endpoint
    )
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


def test_desktop_bootstrap_rejects_unavailable_inherited_fd(
    monkeypatch, capsys
) -> None:
    endpoint = DaemonEndpoint(
        config_path="/tmp/config.json",
        host="127.0.0.1",
        port=4100,
        token="master-token",
    )
    monkeypatch.setattr(
        daemon_command, "resolve_daemon_endpoint", lambda *_a, **_k: endpoint
    )
    monkeypatch.setattr(
        daemon_command.os,
        "fstat",
        lambda _fd: (_ for _ in ()).throw(OSError("closed")),
    )
    monkeypatch.setattr(
        daemon_command,
        "ensure_daemon_running",
        lambda *_a, **_k: pytest.fail("daemon must not start without fd 3"),
    )

    assert daemon_command.daemon_desktop_bootstrap("config.json", fd=3) == 1
    assert capsys.readouterr().err.strip() == (
        "desktop bootstrap failed: daemon_bootstrap_failed"
    )


def test_desktop_bootstrap_rejects_malformed_mint_response(monkeypatch, capsys) -> None:
    endpoint = DaemonEndpoint(
        config_path="/tmp/config.json",
        host="127.0.0.1",
        port=4100,
        token="master-token",
    )
    monkeypatch.setattr(
        daemon_command, "resolve_daemon_endpoint", lambda *_a, **_k: endpoint
    )
    monkeypatch.setattr(
        daemon_command, "ensure_daemon_running", lambda *_a, **_k: endpoint
    )
    monkeypatch.setattr(daemon_command.os, "fstat", lambda _fd: object())
    monkeypatch.setattr(
        daemon_command,
        "daemon_request",
        lambda **_k: (200, {"ok": True, "lease": "malformed"}),
    )
    monkeypatch.setattr(
        daemon_command,
        "_write_readiness_record",
        lambda *_a, **_k: pytest.fail("malformed readiness must not be written"),
    )

    assert daemon_command.daemon_desktop_bootstrap("config.json", fd=3) == 1
    assert capsys.readouterr().err.strip() == (
        "desktop bootstrap failed: daemon_bootstrap_failed"
    )


def test_desktop_bootstrap_reports_config_mismatch(monkeypatch, capsys) -> None:
    endpoint = DaemonEndpoint(
        config_path="/tmp/config.json",
        host="127.0.0.1",
        port=4100,
        token="master-token",
    )
    monkeypatch.setattr(
        daemon_command, "resolve_daemon_endpoint", lambda *_a, **_k: endpoint
    )
    monkeypatch.setattr(daemon_command.os, "fstat", lambda _fd: object())
    monkeypatch.setattr(
        daemon_command,
        "ensure_daemon_running",
        lambda *_a, **_k: (_ for _ in ()).throw(
            daemon_command.DaemonConfigMismatchError("different config")
        ),
    )

    assert daemon_command.daemon_desktop_bootstrap("config.json", fd=3) == 1
    assert capsys.readouterr().err.strip() == (
        "desktop bootstrap failed: daemon_config_mismatch"
    )


def test_desktop_readiness_rejects_config_id_mismatch() -> None:
    endpoint = DaemonEndpoint(
        config_path="/tmp/home/config.json",
        host="127.0.0.1",
        port=4100,
        token="master-token",
        home_root="/tmp/home",
        data_root="/tmp/home/data",
    )
    response = {
        "ok": True,
        "lease": {
            "client_id": "client-1",
            "client_token": "x" * 43,
            "protocol": 1,
            "issued_at": "2026-08-16T00:00:00Z",
            "expires_at": "2999-08-16T12:00:00Z",
            "config_id": "wrong-config",
            "capabilities": ["daemon.health"],
        },
        "meta": {},
    }

    with pytest.raises(RuntimeError, match="config mismatch"):
        daemon_command._desktop_readiness_record(
            endpoint,
            status=200,
            response=response,
            daemon_version=daemon_command._package_version(),
        )


@pytest.mark.parametrize(
    ("capability_overrides", "expected_message"),
    [
        ({"daemon_version": "incompatible"}, "version or capability mismatch"),
        ({"config_id": "wrong-config"}, "version or capability mismatch"),
        ({"protocol": 2}, "version or capability mismatch"),
    ],
)
def test_desktop_bootstrap_rejects_bad_authenticated_capabilities(
    monkeypatch, capability_overrides, expected_message
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
    mint_response = {
        "lease": {
            "client_token": "x" * 43,
            "protocol": 1,
            "config_id": config_id,
        }
    }
    capability_response = {
        "ok": True,
        "daemon_version": daemon_command._package_version(),
        "config_id": config_id,
        "protocol": 1,
        **capability_overrides,
    }
    monkeypatch.setattr(
        daemon_command,
        "daemon_request",
        lambda **_k: (200, capability_response),
    )

    with pytest.raises(RuntimeError, match=expected_message):
        daemon_command._verified_daemon_version(endpoint, mint_response)


def test_start_daemon_propagates_home_and_data_roots(
    monkeypatch, tmp_path: Path
) -> None:
    endpoint = DaemonEndpoint(
        config_path=str(tmp_path / "config.json"),
        host="127.0.0.1",
        port=4100,
        token="master-token",
        home_root=str(tmp_path / "home"),
        data_root=str(tmp_path / "data"),
    )
    captured_env: dict[str, str] = {}
    process = SimpleNamespace(pid=1234)
    monkeypatch.setattr(daemon_command, "load_config", lambda _path: object())
    monkeypatch.setattr(
        daemon_core, "resolve_daemon_pid_file", lambda _config: tmp_path / "daemon.pid"
    )
    monkeypatch.setattr(
        daemon_core, "resolve_daemon_log_file", lambda _config: tmp_path / "daemon.log"
    )
    monkeypatch.setattr(daemon_core, "read_pid", lambda _path: None)
    monkeypatch.setattr(daemon_core, "process_alive", lambda _pid: False)
    monkeypatch.setattr(
        daemon_command,
        "probe_daemon_endpoint",
        lambda *_a, **_k: ("ok", {}),
    )

    def _popen(*_args, **kwargs):
        captured_env.update(kwargs["env"])
        return process

    monkeypatch.setattr(daemon_command.subprocess, "Popen", _popen)

    assert daemon_command._start_daemon(endpoint)["ok"] is True
    assert captured_env["OPENMINION_HOME"] == endpoint.home_root
    assert captured_env["OPENMINION_DATA_ROOT"] == endpoint.data_root
