from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from openminion.cli.presentation.json_output import print_json_payload
from openminion.cli.transport.daemon_client import (
    DaemonEndpoint,
    daemon_is_reachable,
    probe_daemon_endpoint,
    resolve_daemon_endpoint,
)
from openminion.cli.bootstrap.loader import load_config

_PROBE_STATUS_MISMATCH: str = "mismatch"


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
        raise RuntimeError(
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

    if home_root is not None or data_root is not None:
        endpoint = resolve_daemon_endpoint(
            config_path,
            home_root=home_root,
            data_root=data_root,
        )
    else:
        endpoint = resolve_daemon_endpoint(config_path)
    config = load_config(endpoint.config_path)
    pid_file = resolve_daemon_pid_file(config)
    pid = read_pid(pid_file)
    alive = bool(pid and process_alive(pid))
    probe_status, health_payload = probe_daemon_endpoint(endpoint)
    reachable = probe_status == "ok"
    daemon_payload = (
        health_payload.get("daemon") if isinstance(health_payload, dict) else {}
    )
    remote_config_path = ""
    if isinstance(daemon_payload, dict):
        remote_config_path = str(daemon_payload.get("config_path", "")).strip()
    return {
        "ok": reachable,
        "pid": pid,
        "pid_alive": alive,
        "reachable": reachable,
        "status": "running" if reachable else "unreachable",
        "lifecycle": "running" if alive else "stopped",
        "endpoint_status": probe_status,
        "remote_config_path": remote_config_path,
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
        process = subprocess.Popen(  # noqa: S603
            command,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
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
