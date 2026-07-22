from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

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


def run_daemon(args) -> int:
    action = str(getattr(args, "daemon_command", "")).strip().lower()
    if action == "start":
        return daemon_start(args.config)
    if action == "stop":
        return daemon_stop(args.config)
    if action == "status":
        return daemon_status(args.config)
    if action == "logs":
        lines = int(getattr(args, "lines", 200) or 200)
        return daemon_logs(args.config, lines=lines)
    raise RuntimeError("Unknown daemon command")


def ensure_daemon_running(
    config_path: str | None, *, auto_start: bool
) -> DaemonEndpoint:
    endpoint = resolve_daemon_endpoint(config_path)
    probe_status, payload = probe_daemon_endpoint(endpoint)
    if probe_status == "ok":
        return endpoint
    if probe_status == _PROBE_STATUS_MISMATCH:
        remote_config_path = str(
            ((payload.get("daemon") or {}) if isinstance(payload, dict) else {}).get(
                "config_path", ""
            )
        ).strip()
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


def daemon_start(config_path: str | None) -> int:
    endpoint = resolve_daemon_endpoint(config_path)
    result = _start_daemon(endpoint)
    if result["ok"]:
        print(result["message"])
        return 0
    print(result["message"])
    return 1


def daemon_stop(config_path: str | None) -> int:
    from openminion.daemon import process_alive, read_pid, resolve_daemon_pid_file

    endpoint = resolve_daemon_endpoint(config_path)
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


def daemon_status(config_path: str | None) -> int:
    from openminion.daemon import (
        process_alive,
        read_pid,
        resolve_daemon_log_file,
        resolve_daemon_pid_file,
    )

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

    payload = {
        "ok": reachable,
        "pid": pid,
        "pid_alive": alive,
        "reachable": reachable,
        "endpoint_status": probe_status,
        "remote_config_path": remote_config_path,
        "host": endpoint.host,
        "port": endpoint.port,
        "pid_file": str(pid_file),
        "log_file": str(resolve_daemon_log_file(config)),
    }
    print_json_payload(payload)
    return 0 if reachable else 1


def daemon_logs(config_path: str | None, *, lines: int = 200) -> int:
    from openminion.daemon import resolve_daemon_log_file

    endpoint = resolve_daemon_endpoint(config_path)
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
    return 0


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
    if existing_pid and not process_alive(existing_pid):
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

    daemon_status = daemon_subcommands.add_parser("status", help="Show daemon status")
    daemon_status.set_defaults(handler=run_daemon, needs_app=False)

    daemon_logs_cmd = daemon_subcommands.add_parser("logs", help="Show daemon logs")
    daemon_logs_cmd.add_argument(
        "--lines", type=int, default=200, help="Tail line count (default: 200)"
    )
    daemon_logs_cmd.set_defaults(handler=run_daemon, needs_app=False)
