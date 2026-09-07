from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from openminion.cli.presentation.json_output import print_json_payload
from openminion.cli.transport.daemon_client import (
    DaemonEndpoint,
    daemon_is_reachable,
    daemon_request,
    probe_daemon_endpoint,
    resolve_daemon_endpoint,
)
from openminion.api.server.client_auth import build_config_id
from openminion.cli.bootstrap.loader import load_config

_PROBE_STATUS_MISMATCH: str = "mismatch"


class DaemonConfigMismatchError(RuntimeError):
    """The configured daemon endpoint belongs to another config identity."""


def _remote_config_path_from_probe_payload(payload: object) -> str:
    daemon_payload = (payload.get("daemon") or {}) if isinstance(payload, dict) else {}
    return str(daemon_payload.get("config_path", "")).strip()


def run_daemon(args: Any) -> int:
    action = str(getattr(args, "daemon_command", "")).strip().lower()
    if action == "start":
        return daemon_start(
            args.config,
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
        )
    if action == "stop":
        return daemon_stop(
            args.config,
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
        )
    if action == "restart":
        return daemon_restart(
            args.config,
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
        )
    if action == "status":
        return daemon_status(
            args.config,
            as_json=bool(getattr(args, "json", False)),
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
        )
    if action == "logs":
        lines = int(getattr(args, "lines", 200) or 200)
        return daemon_logs(
            args.config,
            lines=lines,
            follow=bool(getattr(args, "follow", False)),
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
        )
    if action == "desktop-bootstrap":
        return daemon_desktop_bootstrap(
            args.config,
            fd=int(getattr(args, "fd", 3)),
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
        )
    raise RuntimeError("Unknown daemon command")


def ensure_daemon_running(
    config_path: str | None,
    *,
    auto_start: bool,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
) -> DaemonEndpoint:
    endpoint = resolve_daemon_endpoint(
        config_path,
        home_root=home_root,
        data_root=data_root,
    )
    probe_status, payload = probe_daemon_endpoint(endpoint)
    if probe_status == "ok":
        return endpoint
    if probe_status == _PROBE_STATUS_MISMATCH:
        remote_config_path = _remote_config_path_from_probe_payload(payload)
        raise DaemonConfigMismatchError(
            "openminion daemon endpoint is occupied by a different config "
            f"(expected {endpoint.config_path}, got {remote_config_path or 'unknown'}). "
            "To recover: (a) stop the running daemon with `openminion daemon stop`, "
            "(b) re-run chat with `--session <name>` to bind a fresh session to the "
            "current config, or (c) use `--reset-session` to clear the stale binding "
            "and retry."
        )
    if not auto_start:
        raise RuntimeError("openminion daemon is not running")
    start_result = _start_daemon(endpoint)
    if not start_result["ok"]:
        raise RuntimeError(start_result["message"])
    return endpoint


def daemon_start(
    config_path: str | None,
    *,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
) -> int:
    endpoint = resolve_daemon_endpoint(
        config_path,
        home_root=home_root,
        data_root=data_root,
    )
    result = _start_daemon(endpoint)
    print(result["message"])
    return 0 if result["ok"] else 1


def daemon_stop(
    config_path: str | None,
    *,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
) -> int:
    from openminion.daemon import process_alive, read_pid, resolve_daemon_pid_file

    endpoint = resolve_daemon_endpoint(
        config_path,
        home_root=home_root,
        data_root=data_root,
    )
    config = load_config(endpoint.config_path)
    pid_file = resolve_daemon_pid_file(config)
    pid = read_pid(pid_file)
    if pid is None:
        if daemon_is_reachable(endpoint):
            print("Daemon appears reachable but no PID file was found.")
            return 1
        print("Daemon is not running.")
        return 0

    if not process_alive(pid):
        _safe_unlink(pid_file)
        print("Removed stale daemon PID file.")
        return 0

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        print(f"Failed to signal daemon process {pid}: {exc}")
        return 1

    deadline = time.time() + 10
    while time.time() < deadline:
        if not process_alive(pid):
            _safe_unlink(pid_file)
            print(f"Stopped daemon pid={pid}")
            return 0
        time.sleep(0.1)

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError as exc:
        print(f"Daemon pid={pid} did not stop within timeout and SIGKILL failed: {exc}")
        return 1

    kill_deadline = time.time() + 5
    while time.time() < kill_deadline:
        if not process_alive(pid):
            _safe_unlink(pid_file)
            print(f"Force-stopped daemon pid={pid} after graceful timeout.")
            return 0
        time.sleep(0.1)

    print(f"Daemon pid={pid} did not stop within timeout (including SIGKILL).")
    return 1


def daemon_restart(
    config_path: str | None,
    *,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
) -> int:
    stop_code = daemon_stop(config_path, home_root=home_root, data_root=data_root)
    if stop_code != 0:
        return stop_code
    return daemon_start(config_path, home_root=home_root, data_root=data_root)


def daemon_status(
    config_path: str | None,
    *,
    as_json: bool = True,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
) -> int:
    payload = _build_daemon_status_payload(
        config_path,
        home_root=home_root,
        data_root=data_root,
    )
    if as_json:
        print_json_payload(payload)
    else:
        print(
            "daemon status: "
            f"status={payload['status']} lifecycle={payload['lifecycle']} "
            f"endpoint={payload['host']}:{payload['port']} pid={payload['pid'] or '-'}"
        )
        print(f"config: expected={payload['config_path']}")
        remote_config_path = str(payload.get("remote_config_path", "") or "")
        if remote_config_path and remote_config_path != payload["config_path"]:
            print(f"config mismatch: running={remote_config_path}")
        print(f"pid_file: {payload['pid_file']}")
        print(f"log_file: {payload['log_file']}")
    return 0 if bool(payload.get("reachable", False)) else 1


def _build_daemon_status_payload(
    config_path: str | None,
    *,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
) -> dict[str, object]:
    from openminion.daemon import (
        process_alive,
        read_pid,
        resolve_daemon_log_file,
        resolve_daemon_pid_file,
    )
    from openminion.base.config import ConfigManager

    if home_root is not None or data_root is not None:
        endpoint = resolve_daemon_endpoint(
            config_path,
            home_root=home_root,
            data_root=data_root,
        )
    else:
        endpoint = resolve_daemon_endpoint(config_path)
    manager = ConfigManager.load(
        endpoint.config_path,
        home_root=Path(home_root).expanduser().resolve() if home_root else None,
        data_root=Path(data_root).expanduser().resolve() if data_root else None,
    )
    config = manager.base_config
    pid_file = resolve_daemon_pid_file(config)
    pid = read_pid(pid_file)
    alive = bool(pid and process_alive(pid))
    probe_status, health_payload = probe_daemon_endpoint(endpoint)
    reachable = probe_status == "ok"
    daemon_payload = (
        health_payload.get("daemon") if isinstance(health_payload, dict) else {}
    )
    remote_config_path = ""
    remote_data_root = ""
    if isinstance(daemon_payload, dict):
        remote_config_path = str(daemon_payload.get("config_path", "")).strip()
        remote_data_root = str(daemon_payload.get("data_root", "")).strip()
    from openminion.modules.task.scheduling.coordination import (
        scheduler_readiness_from_health,
    )

    identity_matches = None
    if remote_config_path and remote_data_root:
        identity_matches = remote_config_path == endpoint.config_path and Path(
            remote_data_root
        ).resolve(strict=False) == manager.data_root.resolve(strict=False)
    return {
        "ok": reachable,
        "pid": pid,
        "pid_alive": alive,
        "reachable": reachable,
        "status": "running" if reachable else "unreachable",
        "lifecycle": "running" if alive else "stopped",
        "endpoint_status": probe_status,
        "remote_config_path": remote_config_path,
        "remote_data_root": remote_data_root,
        "scheduler": scheduler_readiness_from_health(
            health_payload if isinstance(health_payload, dict) else {},
            reachable=probe_status in {"ok", _PROBE_STATUS_MISMATCH},
            identity_matches=identity_matches,
        ),
        "host": endpoint.host,
        "port": endpoint.port,
        "config_path": endpoint.config_path,
        "pid_file": str(pid_file),
        "log_file": str(resolve_daemon_log_file(config)),
    }


def daemon_logs(
    config_path: str | None,
    *,
    lines: int = 200,
    follow: bool = False,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
) -> int:
    from openminion.daemon import resolve_daemon_log_file

    endpoint = resolve_daemon_endpoint(
        config_path,
        home_root=home_root,
        data_root=data_root,
    )
    config = load_config(endpoint.config_path)
    log_file = resolve_daemon_log_file(config)
    if not log_file.exists():
        print(f"Log file does not exist: {log_file}")
        return 1

    safe_lines = max(1, int(lines))
    text = log_file.read_text(encoding="utf-8", errors="replace")
    chunks = text.splitlines()
    tail = chunks[-safe_lines:]
    for line in tail:
        print(line)
    if follow:
        _follow_log_file(
            log_file, start_offset=len(text.encode("utf-8", errors="replace"))
        )
    return 0


def daemon_desktop_bootstrap(
    config_path: str | None,
    *,
    fd: int = 3,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
) -> int:
    if fd != 3:
        print("desktop bootstrap failed: invalid_readiness_fd", file=sys.stderr)
        return 1
    endpoint = resolve_daemon_endpoint(
        config_path,
        home_root=home_root,
        data_root=data_root,
    )
    if not endpoint.token:
        print("desktop bootstrap failed: desktop_ipc_token_required", file=sys.stderr)
        return 1
    try:
        os.fstat(fd)
        endpoint = ensure_daemon_running(
            config_path,
            auto_start=True,
            home_root=home_root,
            data_root=data_root,
        )
        status, response = daemon_request(
            endpoint=endpoint,
            method="POST",
            path="/v1/client/leases",
            payload={
                "schema_version": 1,
                "client": {
                    "kind": "desktop",
                    "version": _package_version(),
                    "protocol_min": 1,
                    "protocol_max": 1,
                },
                "requested_ttl_seconds": 43_200,
            },
            timeout_s=15.0,
            max_response_bytes=64 * 1024,
        )
        daemon_version = _verified_daemon_version(endpoint, response)
        record = _desktop_readiness_record(
            endpoint,
            status=status,
            response=response,
            daemon_version=daemon_version,
        )
        _write_readiness_record(fd, record)
    except DaemonConfigMismatchError:
        print("desktop bootstrap failed: daemon_config_mismatch", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, TypeError, ValueError):
        print("desktop bootstrap failed: daemon_bootstrap_failed", file=sys.stderr)
        return 1
    return 0


def _desktop_readiness_record(
    endpoint: DaemonEndpoint,
    *,
    status: int,
    response: dict[str, Any],
    daemon_version: str,
) -> dict[str, object]:
    if status != 200 or response.get("ok") is not True:
        raise RuntimeError("lease mint failed")
    if set(response) != {"ok", "lease", "meta"}:
        raise RuntimeError("unexpected lease response")
    meta = response.get("meta")
    if not isinstance(meta, dict) or not set(meta).issubset(
        {"request_id", "method", "path"}
    ):
        raise RuntimeError("invalid response metadata")
    lease = response.get("lease")
    if not isinstance(lease, dict) or set(lease) != {
        "client_id",
        "client_token",
        "protocol",
        "issued_at",
        "expires_at",
        "config_id",
        "capabilities",
    }:
        raise RuntimeError("invalid lease payload")
    expected_config_id = build_config_id(
        endpoint.config_path,
        endpoint.home_root,
        endpoint.data_root,
    )
    if lease.get("config_id") != expected_config_id:
        raise RuntimeError("daemon config mismatch")
    client_id = str(lease.get("client_id") or "")
    client_token = str(lease.get("client_token") or "")
    expires_at = str(lease.get("expires_at") or "")
    capabilities = lease.get("capabilities")
    if not client_id or len(client_token) < 43:
        raise RuntimeError("invalid lease identity")
    if lease.get("protocol") != 1 or not _future_timestamp(expires_at):
        raise RuntimeError("invalid lease version or expiry")
    if not isinstance(capabilities, list) or not all(
        isinstance(value, str) and value for value in capabilities
    ):
        raise RuntimeError("invalid capabilities")
    if endpoint.host not in {"127.0.0.1", "::1"} or not 1 <= endpoint.port <= 65_535:
        raise RuntimeError("invalid daemon endpoint")
    return {
        "schema_version": 1,
        "event": "desktop.client.ready",
        "host": endpoint.host,
        "port": endpoint.port,
        "protocol_min": 1,
        "protocol_max": 1,
        "daemon_version": daemon_version,
        "config_id": expected_config_id,
        "client_id": client_id,
        "client_token": client_token,
        "expires_at": expires_at,
    }


def _verified_daemon_version(
    endpoint: DaemonEndpoint,
    mint_response: dict[str, Any],
) -> str:
    lease = mint_response.get("lease")
    if not isinstance(lease, dict):
        raise RuntimeError("invalid lease payload")
    status, payload = daemon_request(
        endpoint=replace(
            endpoint,
            token="",
            client_token=str(lease.get("client_token") or ""),
        ),
        method="GET",
        path="/v1/client/capabilities",
        timeout_s=15.0,
        max_response_bytes=64 * 1024,
    )
    daemon_version = str(payload.get("daemon_version") or "")
    if (
        status != 200
        or payload.get("ok") is not True
        or payload.get("config_id") != lease.get("config_id")
        or payload.get("protocol") != lease.get("protocol")
        or daemon_version != _package_version()
    ):
        raise RuntimeError("daemon version or capability mismatch")
    return daemon_version


def _future_timestamp(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed > datetime.now(UTC)


def _write_readiness_record(fd: int, record: dict[str, object]) -> None:
    encoded = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > 16 * 1024:
        raise RuntimeError("readiness record is too large")
    offset = 0
    while offset < len(encoded):
        written = os.write(fd, encoded[offset:])
        if written <= 0:
            raise RuntimeError("readiness fd closed")
        offset += written


def _package_version() -> str:
    try:
        return version("openminion")
    except PackageNotFoundError:
        return "0.0.0"


def _follow_log_file(log_file: Path, *, start_offset: int) -> None:
    offset = max(0, start_offset)
    try:
        while True:
            with log_file.open("r", encoding="utf-8", errors="replace") as stream:
                stream.seek(offset)
                chunk = stream.read()
                offset = stream.tell()
            if chunk:
                print(chunk, end="")
            time.sleep(0.5)
    except KeyboardInterrupt:
        return


def _start_daemon(endpoint: DaemonEndpoint) -> dict[str, object]:
    from openminion.daemon import (
        process_alive,
        read_pid,
        resolve_daemon_log_file,
        resolve_daemon_pid_file,
    )

    config = load_config(endpoint.config_path)
    pid_file = resolve_daemon_pid_file(config)
    log_file = resolve_daemon_log_file(config)

    pid_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    existing_pid = read_pid(pid_file)
    if existing_pid and process_alive(existing_pid):
        probe_status, payload = probe_daemon_endpoint(endpoint)
        if probe_status == "ok":
            return {
                "ok": True,
                "message": f"Daemon already running pid={existing_pid} ({endpoint.host}:{endpoint.port})",
            }
        if probe_status == _PROBE_STATUS_MISMATCH:
            remote_config_path = _remote_config_path_from_probe_payload(payload)
            return {
                "ok": False,
                "message": (
                    "Daemon port is occupied by a different config "
                    f"(expected {endpoint.config_path}, got {remote_config_path or 'unknown'})."
                ),
            }
        return {
            "ok": False,
            "message": f"PID file exists for running process {existing_pid}, but daemon is unreachable.",
        }
    if existing_pid:
        _safe_unlink(pid_file)

    command = [
        sys.executable,
        "-m",
        "openminion.daemon",
        "serve",
        "--config",
        endpoint.config_path,
        "--host",
        endpoint.host,
        "--port",
        str(endpoint.port),
        "--pid-file",
        str(pid_file),
    ]

    with log_file.open("a", encoding="utf-8") as stream:
        daemon_env = os.environ.copy()
        if endpoint.home_root:
            daemon_env["OPENMINION_HOME"] = endpoint.home_root
        if endpoint.data_root:
            daemon_env["OPENMINION_DATA_ROOT"] = endpoint.data_root
        process = subprocess.Popen(  # noqa: S603
            command,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env=daemon_env,
        )

    deadline = time.time() + 10
    while time.time() < deadline:
        probe_status, payload = probe_daemon_endpoint(endpoint, timeout_s=1.5)
        if probe_status == "ok":
            pid = read_pid(pid_file) or process.pid
            return {
                "ok": True,
                "message": f"Started daemon pid={pid} ({endpoint.host}:{endpoint.port})",
            }
        if probe_status == _PROBE_STATUS_MISMATCH:
            remote_config_path = _remote_config_path_from_probe_payload(payload)
            return {
                "ok": False,
                "message": (
                    "Daemon port became reachable, but the endpoint identity does not match "
                    f"the requested config (expected {endpoint.config_path}, got "
                    f"{remote_config_path or 'unknown'})."
                ),
            }
        if process.poll() is not None:
            break
        time.sleep(0.1)

    return {
        "ok": False,
        "message": (
            "Daemon failed to become healthy within timeout. "
            f"Inspect logs at {log_file}."
        ),
    }


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    daemon = subparsers.add_parser("daemon", help="Daemon lifecycle controls")
    daemon_subcommands = daemon.add_subparsers(dest="daemon_command")

    daemon_start = daemon_subcommands.add_parser(
        "start", help="Start openminiond in the background"
    )
    daemon_start.set_defaults(handler=run_daemon, needs_app=False)

    daemon_stop_cmd = daemon_subcommands.add_parser("stop", help="Stop openminiond")
    daemon_stop_cmd.set_defaults(handler=run_daemon, needs_app=False)

    daemon_restart_cmd = daemon_subcommands.add_parser(
        "restart", help="Stop and start openminiond"
    )
    daemon_restart_cmd.set_defaults(handler=run_daemon, needs_app=False)

    daemon_status = daemon_subcommands.add_parser("status", help="Show daemon status")
    daemon_status.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable daemon status JSON",
    )
    daemon_status.set_defaults(handler=run_daemon, needs_app=False)

    daemon_logs_cmd = daemon_subcommands.add_parser("logs", help="Show daemon logs")
    daemon_logs_cmd.add_argument(
        "--lines", type=int, default=200, help="Tail line count (default: 200)"
    )
    daemon_logs_cmd.add_argument(
        "--follow",
        "-f",
        action="store_true",
        help="Keep streaming appended daemon log lines",
    )
    daemon_logs_cmd.set_defaults(handler=run_daemon, needs_app=False)

    desktop_bootstrap = daemon_subcommands.add_parser(
        "desktop-bootstrap",
        help="Start or attach to the daemon and write a desktop lease to fd 3",
    )
    desktop_bootstrap.add_argument(
        "--fd",
        type=int,
        default=3,
        help="Inherited readiness descriptor (must be 3)",
    )
    desktop_bootstrap.set_defaults(handler=run_daemon, needs_app=False)
