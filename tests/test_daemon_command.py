from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import openminion.daemon as daemon_core
from openminion.base.config import ConfigManager, OpenMinionConfig, save_config
from openminion.cli.commands import daemon as daemon_command
from openminion.cli.transport.daemon_client import DaemonEndpoint


def test_daemon_status_json_output_when_reachable(monkeypatch, capsys) -> None:
    endpoint = DaemonEndpoint(
        host="127.0.0.1",
        port=4100,
        config_path="/tmp/openminion.json",
        token="master-token",
    )
    config = SimpleNamespace(runtime=SimpleNamespace(ipc_token="master-token"))
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
        "desktop": {
            "configured": True,
            "protocol_max": 1,
            "protocol_min": 1,
            "reason": "ready",
        },
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
    endpoint = DaemonEndpoint(
        host="127.0.0.1",
        port=4100,
        config_path="/tmp/openminion.json",
    )
    config = SimpleNamespace(runtime=SimpleNamespace(ipc_token=""))
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
    assert payload["desktop"] == {
        "configured": False,
        "protocol_max": 1,
        "protocol_min": 1,
        "reason": "ipc_token_missing",
    }


def test_daemon_status_text_includes_redacted_desktop_readiness(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        daemon_command,
        "_build_daemon_status_payload",
        lambda *_args, **_kwargs: {
            "status": "running",
            "lifecycle": "running",
            "pid": 4321,
            "pid_alive": True,
            "reachable": True,
            "host": "127.0.0.1",
            "port": 4100,
            "config_path": "/tmp/openminion.json",
            "remote_config_path": "/tmp/openminion.json",
            "pid_file": "/tmp/openminion.pid",
            "log_file": "/tmp/openminion.log",
            "desktop": {
                "configured": True,
                "reason": "ready",
                "protocol_min": 1,
                "protocol_max": 1,
            },
        },
    )

    assert daemon_command.daemon_status("config.json", as_json=False) == 0
    output = capsys.readouterr().out
    assert "desktop: configured=True reason=ready protocol=1..1" in output
    assert "master-token" not in output


def _desktop_setup_config(
    tmp_path: Path,
    *,
    token: str = "",
    host: str = "127.0.0.1",
) -> tuple[OpenMinionConfig, Path, DaemonEndpoint]:
    config = OpenMinionConfig()
    config.runtime.ipc_token = token
    config.runtime.env["OPENMINION_DATA_ROOT"] = str(tmp_path / "data")
    path = tmp_path / "config.json"
    save_config(config, str(path))
    endpoint = DaemonEndpoint(
        config_path=str(path),
        host=host,
        port=4100,
        token=token.strip(),
    )
    return config, path, endpoint


def test_desktop_setup_generates_redacted_owner_only_token(
    tmp_path, monkeypatch, capsys
) -> None:
    config, config_path, endpoint = _desktop_setup_config(tmp_path)
    config_path.chmod(0o644)
    monkeypatch.setattr(
        daemon_command, "resolve_daemon_endpoint", lambda *_a, **_k: endpoint
    )
    monkeypatch.setattr(daemon_command, "load_config", lambda _path: config)
    monkeypatch.setattr(
        daemon_command, "probe_daemon_endpoint", lambda _endpoint: ("unreachable", {})
    )
    monkeypatch.setattr(daemon_command, "is_git_tracked", lambda _path: False)

    assert daemon_command.daemon_desktop_setup(str(config_path)) == 0

    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    token = persisted["runtime"]["ipc_token"]
    output = capsys.readouterr().out
    assert isinstance(token, str) and len(token) >= 43
    assert token not in output
    assert "Token: [redacted]" in output
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_desktop_setup_replaces_whitespace_only_token(
    tmp_path, monkeypatch, capsys
) -> None:
    config, config_path, endpoint = _desktop_setup_config(tmp_path, token="   ")
    monkeypatch.setattr(
        daemon_command, "resolve_daemon_endpoint", lambda *_a, **_k: endpoint
    )
    monkeypatch.setattr(daemon_command, "load_config", lambda _path: config)
    monkeypatch.setattr(
        daemon_command, "probe_daemon_endpoint", lambda _endpoint: ("unreachable", {})
    )
    monkeypatch.setattr(daemon_command, "is_git_tracked", lambda _path: False)

    assert daemon_command.daemon_desktop_setup(str(config_path)) == 0
    token = json.loads(config_path.read_text(encoding="utf-8"))["runtime"]["ipc_token"]
    assert token.strip() == token
    assert len(token) >= 43
    assert token not in capsys.readouterr().out


def test_desktop_setup_is_idempotent_without_probe_or_write(
    tmp_path, monkeypatch, capsys
) -> None:
    config, config_path, endpoint = _desktop_setup_config(
        tmp_path, token="existing-token"
    )
    config_path.chmod(0o644)
    monkeypatch.setattr(
        daemon_command, "resolve_daemon_endpoint", lambda *_a, **_k: endpoint
    )
    monkeypatch.setattr(daemon_command, "load_config", lambda _path: config)
    monkeypatch.setattr(
        daemon_command,
        "probe_daemon_endpoint",
        lambda _endpoint: pytest.fail("idempotent setup must not probe"),
    )
    monkeypatch.setattr(
        daemon_command,
        "atomic_save_setup_config",
        lambda *_a: pytest.fail("idempotent setup must not write"),
    )

    assert daemon_command.daemon_desktop_setup(str(config_path)) == 0
    output = capsys.readouterr().out
    assert "existing-token" not in output
    assert "already configured" in output
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


@pytest.mark.parametrize("token", ["", "existing-token"])
def test_desktop_setup_refuses_non_loopback_endpoint(
    tmp_path, monkeypatch, capsys, token
) -> None:
    config, config_path, endpoint = _desktop_setup_config(
        tmp_path,
        token=token,
        host="0.0.0.0",
    )
    monkeypatch.setattr(
        daemon_command, "resolve_daemon_endpoint", lambda *_a, **_k: endpoint
    )
    monkeypatch.setattr(daemon_command, "load_config", lambda _path: config)
    monkeypatch.setattr(
        daemon_command,
        "probe_daemon_endpoint",
        lambda _endpoint: pytest.fail("non-loopback setup must not probe"),
    )
    monkeypatch.setattr(
        daemon_command,
        "atomic_save_setup_config",
        lambda *_a: pytest.fail("non-loopback setup must not write"),
    )

    assert daemon_command.daemon_desktop_setup(str(config_path)) == 1
    assert "explicit loopback" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("token", "rotate", "endpoint_status"),
    [
        ("", False, "ok"),
        ("existing-token", True, "ok"),
        ("", False, "mismatch"),
    ],
)
def test_desktop_setup_refuses_token_mutation_while_endpoint_is_occupied(
    tmp_path, monkeypatch, capsys, token, rotate, endpoint_status
) -> None:
    config, config_path, endpoint = _desktop_setup_config(tmp_path, token=token)
    monkeypatch.setattr(
        daemon_command, "resolve_daemon_endpoint", lambda *_a, **_k: endpoint
    )
    monkeypatch.setattr(daemon_command, "load_config", lambda _path: config)
    monkeypatch.setattr(
        daemon_command,
        "probe_daemon_endpoint",
        lambda _endpoint: (endpoint_status, {}),
    )
    monkeypatch.setattr(
        daemon_command,
        "atomic_save_setup_config",
        lambda *_a: pytest.fail("occupied endpoint must not rewrite the token"),
    )

    assert daemon_command.daemon_desktop_setup(str(config_path), rotate=rotate) == 1
    assert "requires the selected daemon to be stopped" in capsys.readouterr().out


def test_desktop_setup_refuses_alive_unreachable_daemon(
    tmp_path, monkeypatch, capsys
) -> None:
    config, config_path, endpoint = _desktop_setup_config(tmp_path)
    monkeypatch.setattr(
        daemon_command, "resolve_daemon_endpoint", lambda *_a, **_k: endpoint
    )
    monkeypatch.setattr(daemon_command, "load_config", lambda _path: config)
    monkeypatch.setattr(
        daemon_core, "resolve_daemon_pid_file", lambda _cfg: config_path
    )
    monkeypatch.setattr(daemon_core, "read_pid", lambda _path: 4321)
    monkeypatch.setattr(daemon_core, "process_alive", lambda _pid: True)
    monkeypatch.setattr(
        daemon_command,
        "probe_daemon_endpoint",
        lambda _endpoint: pytest.fail("a live PID must refuse before probing"),
    )

    assert daemon_command.daemon_desktop_setup(str(config_path)) == 1
    assert "requires the selected daemon to be stopped" in capsys.readouterr().out


def test_desktop_setup_rotates_only_when_stopped(tmp_path, monkeypatch, capsys) -> None:
    config, config_path, endpoint = _desktop_setup_config(
        tmp_path, token="existing-token"
    )
    monkeypatch.setattr(
        daemon_command, "resolve_daemon_endpoint", lambda *_a, **_k: endpoint
    )
    monkeypatch.setattr(daemon_command, "load_config", lambda _path: config)
    monkeypatch.setattr(
        daemon_command, "probe_daemon_endpoint", lambda _endpoint: ("unreachable", {})
    )
    monkeypatch.setattr(daemon_command, "is_git_tracked", lambda _path: False)

    assert daemon_command.daemon_desktop_setup(str(config_path), rotate=True) == 0
    rotated = json.loads(config_path.read_text(encoding="utf-8"))["runtime"][
        "ipc_token"
    ]
    output = capsys.readouterr().out
    assert rotated != "existing-token"
    assert rotated not in output
    assert "Rotated Desktop access" in output


def test_desktop_setup_requires_acknowledgement_for_tracked_config(
    tmp_path, monkeypatch, capsys
) -> None:
    config, config_path, endpoint = _desktop_setup_config(tmp_path)
    monkeypatch.setattr(
        daemon_command, "resolve_daemon_endpoint", lambda *_a, **_k: endpoint
    )
    monkeypatch.setattr(daemon_command, "load_config", lambda _path: config)
    monkeypatch.setattr(
        daemon_command, "probe_daemon_endpoint", lambda _endpoint: ("unreachable", {})
    )
    monkeypatch.setattr(daemon_command, "is_git_tracked", lambda _path: True)

    assert daemon_command.daemon_desktop_setup(str(config_path)) == 1
    assert config.runtime.ipc_token == ""
    assert "git-tracked config" in capsys.readouterr().out

    assert (
        daemon_command.daemon_desktop_setup(str(config_path), allow_tracked_secret=True)
        == 0
    )
    token = json.loads(config_path.read_text(encoding="utf-8"))["runtime"]["ipc_token"]
    assert token not in capsys.readouterr().out


def test_desktop_setup_returns_redacted_write_failure(
    tmp_path, monkeypatch, capsys
) -> None:
    config, config_path, endpoint = _desktop_setup_config(tmp_path)
    monkeypatch.setattr(
        daemon_command, "resolve_daemon_endpoint", lambda *_a, **_k: endpoint
    )
    monkeypatch.setattr(daemon_command, "load_config", lambda _path: config)
    monkeypatch.setattr(
        daemon_command, "probe_daemon_endpoint", lambda _endpoint: ("unreachable", {})
    )
    monkeypatch.setattr(daemon_command, "is_git_tracked", lambda _path: False)

    def fail_write(*_args) -> None:
        raise PermissionError("permission denied")

    monkeypatch.setattr(daemon_command, "atomic_save_setup_config", fail_write)

    assert daemon_command.daemon_desktop_setup(str(config_path)) == 1
    output = capsys.readouterr().out
    assert "permission denied" in output
    assert config.runtime.ipc_token not in output


@pytest.mark.parametrize(
    ("token", "host", "configured", "reason"),
    [
        ("master-token", "127.0.0.1", True, "ready"),
        ("", "127.0.0.1", False, "ipc_token_missing"),
        ("master-token", "0.0.0.0", False, "non_loopback_endpoint"),
    ],
)
def test_desktop_configuration_status(token, host, configured, reason) -> None:
    endpoint = DaemonEndpoint(
        config_path="/tmp/config.json",
        host=host,
        port=4100,
        token=token.strip(),
    )

    assert daemon_command._desktop_configuration_status(endpoint) == {
        "configured": configured,
        "reason": reason,
        "protocol_min": 1,
        "protocol_max": 1,
    }


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
