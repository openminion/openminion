from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil

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


_SERVE_IDENTITY_FLAGS: tuple[str, ...] = ("--config", "--host", "--port", "--pid-file")


def _serve_identity(argv: list[str]) -> tuple[dict[str, str] | None, str]:
    """Parse `python -m openminion.daemon serve …` into its identity fields.

    Returns the fields, or ``None`` and the reason the argv is not an unambiguous
    daemon launch. This runs before any signal, so anything unexpected is a refusal:
    guessing here means terminating a process that is not ours.
    """
    interpreter = Path(argv[0]).name if argv else ""
    if not interpreter.startswith("python"):
        return (
            None,
            f"executable {interpreter or 'unknown'} is not a Python interpreter",
        )
    if argv[1:4] != ["-m", "openminion.daemon", "serve"]:
        return None, "command line does not launch `openminion.daemon serve`"
    values: dict[str, str] = {}
    index = 4
    while index < len(argv):
        flag = argv[index]
        if flag not in _SERVE_IDENTITY_FLAGS:
            return None, f"unexpected argument {flag!r}"
        if flag in values:
            return None, f"duplicate {flag}"
        if index + 1 >= len(argv):
            return None, f"missing value for {flag}"
        values[flag] = argv[index + 1]
        index += 2
    missing = [flag for flag in _SERVE_IDENTITY_FLAGS if flag not in values]
    if missing:
        return None, f"missing {', '.join(missing)}"
    return {
        "config": values["--config"],
        "host": values["--host"],
        "port": values["--port"],
        "pid_file": values["--pid-file"],
    }, ""


def _process_executable(process: psutil.Process) -> str | None:
    """The resolved executable of the process, or ``None`` when it cannot be read."""
    try:
        return str(process.exe() or "").strip() or None
    except psutil.Error:
        return None


# A macOS framework interpreter lives at ``<framework>/Versions/<x.y>/bin/pythonX.Y``
# and reports its launcher as one of two exact paths below that same version root.
_FRAMEWORK_INSTALL = re.compile(
    r"^(?P<root>.+/Python\.framework)/Versions/(?P<version>\d+\.\d+)/",
    re.IGNORECASE,
)
_FRAMEWORK_LAUNCHER_SUFFIXES = (
    "Resources/Python.app/Contents/MacOS/Python",
    "Python.app/Contents/MacOS/Python",
)


def _resolved_executable(path_text: str) -> str:
    """Canonical file behind an executable path, or ``""`` when it cannot be resolved."""
    if not path_text.strip():
        return ""
    try:
        return str(Path(path_text).expanduser().resolve())
    except OSError:
        return ""


def _is_this_interpreter(exe: str) -> bool:
    """True when ``exe`` is this interpreter, or the launcher of its own installation.

    Identity is a resolved file path, never a name or a prefix: a bare ``python3``
    elsewhere, a shared ancestor directory and a nested framework tree are all refused.
    """
    resolved_exe = _resolved_executable(exe)
    if resolved_exe and resolved_exe == _resolved_executable(sys.executable):
        return True
    install = _FRAMEWORK_INSTALL.match(_resolved_executable(sys.executable))
    if install is None or not resolved_exe:
        return False
    version_root = f"{install.group('root')}/Versions/{install.group('version')}"
    accepted = {f"{version_root}/{suffix}" for suffix in _FRAMEWORK_LAUNCHER_SUFFIXES}
    return resolved_exe in accepted


def _resolved_path_text(path_text: str) -> str:
    """Comparable form of a launch path.

    Relative paths are left as-is: the daemon was launched from a directory this
    command does not know, so resolving them against the caller's cwd would compare
    two different files.
    """
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        return str(path)
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _stop_identity_refusal(
    argv: list[str],
    exe: str | None,
    *,
    endpoint: DaemonEndpoint,
    pid_file: Path,
    pid: int,
) -> str | None:
    """Return the refusal message when PID does not provably own this daemon."""
    identity, reason = _serve_identity(argv)
    if identity is None:
        return f"PID {pid} does not match this daemon's launch form: {reason}. No process was signalled."
    if exe is None:
        return (
            f"PID {pid} executable identity could not be read, so it cannot be "
            "proven to be the daemon this command launched. No process was signalled."
        )
    if not _is_this_interpreter(exe):
        return (
            f"PID {pid} runs {exe}, which does not match the interpreter this command "
            f"runs ({sys.executable}). No process was signalled."
        )
    for flag, observed, expected in (
        ("--config", identity["config"], endpoint.config_path),
        ("--pid-file", identity["pid_file"], str(pid_file)),
    ):
        observed_path = _resolved_path_text(observed)
        expected_path = _resolved_path_text(expected)
        if observed_path != expected_path:
            return (
                f"PID {pid} was launched with {flag} {observed_path}, but this daemon "
                f"resolves to {expected_path}. No process was signalled."
            )
    if identity["host"] != endpoint.host or identity["port"] != str(endpoint.port):
        return (
            f"PID {pid} was launched for {identity['host']}:{identity['port']}, but this "
            f"daemon resolves to {endpoint.host}:{endpoint.port}. No process was signalled."
        )
    return None


def _await_daemon_exit(process: psutil.Process, *, pid: int, pid_file: Path) -> int:
    """Wait for a signalled daemon to go away, force-killing after the deadline."""
    from openminion.daemon import process_alive

    deadline = time.time() + 10
    while time.time() < deadline:
        if not process_alive(pid):
            _unlink_owned(pid_file, pid)
            print(f"Stopped daemon pid={pid}")
            return 0
        time.sleep(0.1)

    try:
        process.kill()
    except psutil.Error as exc:
        print(
            f"Daemon pid={pid} did not stop within timeout and force kill failed: {exc}"
        )
        return 1

    kill_deadline = time.time() + 5
    while time.time() < kill_deadline:
        if not process_alive(pid):
            _unlink_owned(pid_file, pid)
            print(f"Force-stopped daemon pid={pid} after graceful timeout.")
            return 0
        time.sleep(0.1)

    print(f"Daemon pid={pid} did not stop within timeout (including force kill).")
    return 1


def _endpoint_identity_refusal(endpoint: DaemonEndpoint) -> str | None:
    """Refuse when a reachable endpoint answers for a different config.

    The probe status is the contract: ``mismatch`` is a contradiction, and anything
    else leaves the PID checks in charge.
    """
    probe_status, payload = probe_daemon_endpoint(endpoint)
    if probe_status != _PROBE_STATUS_MISMATCH:
        return None
    remote_config_path = _remote_config_path_from_probe_payload(payload)
    return (
        f"Daemon endpoint {endpoint.host}:{endpoint.port} identifies config "
        f"{remote_config_path or 'unknown'}, but this command resolves to "
        f"{endpoint.config_path}. No process was signalled."
    )


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
    pid_file = resolve_daemon_pid_file(
        config,
        home_root=endpoint.home_root or None,
        data_root=endpoint.data_root or None,
    )
    pid = read_pid(pid_file)
    if pid is None:
        if daemon_is_reachable(endpoint):
            print(
                f"Daemon endpoint {endpoint.host}:{endpoint.port} is reachable, but no "
                f"daemon PID file was found at {pid_file}. No process was signalled."
            )
            print(
                "Run `openminion daemon status --json` to inspect reachability, PID and "
                "lifecycle state; if the listener is not the daemon for this config, stop "
                "it manually."
            )
            return 1
        print("Daemon is not running.")
        return 0

    if not process_alive(pid):
        if _unlink_owned(pid_file, pid):
            print("Removed stale daemon PID file.")
        else:
            print(
                f"Stale PID file {pid_file} was left in place: it no longer names "
                f"PID {pid}."
            )
        return 0

    endpoint_refusal = _endpoint_identity_refusal(endpoint)
    if endpoint_refusal is not None:
        print(endpoint_refusal)
        print(
            "Run `openminion daemon status --json` to inspect reachability, PID and "
            "lifecycle state; if the listener is not the daemon for this config, stop "
            "it manually."
        )
        return 1

    try:
        process = psutil.Process(pid)
        refusal = _stop_identity_refusal(
            list(process.cmdline()),
            _process_executable(process),
            endpoint=endpoint,
            pid_file=pid_file,
            pid=pid,
        )
    except psutil.Error as exc:
        print(f"Failed to inspect daemon process {pid}: {exc}")
        return 1

    if refusal is not None:
        print(refusal)
        print(
            "Run `openminion daemon status --json` to inspect reachability, PID and "
            "lifecycle state; if the listener is not the daemon for this config, stop "
            "it manually."
        )
        return 1

    # The reread confirms the PID file still names this process; a replacement that
    # appears after this read but before termination is not covered by the check.
    if read_pid(pid_file) != pid:
        print(
            f"PID file {pid_file} changed while verifying PID {pid}. "
            "No process was signalled."
        )
        return 1

    # The same Process instance inspected above must still be alive: a process that
    # exited between the reread and this call is not something to signal.
    if not process.is_running():
        _unlink_owned(pid_file, pid)
        print(f"Daemon pid={pid} exited before it could be signalled.")
        return 0

    try:
        process.terminate()
    except psutil.Error as exc:
        print(f"Failed to terminate daemon process {pid}: {exc}")
        return 1

    return _await_daemon_exit(process, pid=pid, pid_file=pid_file)


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
    payload = build_daemon_status_payload(
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


def build_daemon_status_payload(
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
    pid_file = resolve_daemon_pid_file(config, home_root=home_root, data_root=data_root)
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
        "log_file": str(
            resolve_daemon_log_file(config, home_root=home_root, data_root=data_root)
        ),
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
    log_file = resolve_daemon_log_file(
        config,
        home_root=endpoint.home_root or None,
        data_root=endpoint.data_root or None,
    )
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


def _await_daemon_ready(
    endpoint: DaemonEndpoint,
    process: subprocess.Popen[bytes],
    *,
    pid_file: Path,
    log_file: Path,
) -> dict[str, object]:
    """Probe a freshly spawned daemon until it answers, exits, or runs out of time."""
    from openminion.daemon import read_pid

    deadline = time.time() + 10
    exit_code: int | None = None
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
            exit_code = process.returncode
            break
        time.sleep(0.1)

    if exit_code is None:
        # The last probe can see a live child that exits during the sleep; read the
        # status once more so the message reflects what actually happened.
        exit_code = process.poll()
    if exit_code is not None:
        log_hint = (
            f"Inspect logs at {log_file}."
            if log_file.exists()
            else "No daemon log file was written."
        )
        return {
            "ok": False,
            "message": (
                f"Daemon exited with code {exit_code} before answering on "
                f"{endpoint.host}:{endpoint.port}. {log_hint}"
            ),
        }
    return {
        "ok": False,
        "message": (
            "Daemon failed to become healthy within timeout. "
            f"Inspect logs at {log_file}."
        ),
    }


def _start_daemon(endpoint: DaemonEndpoint) -> dict[str, object]:
    from openminion.daemon import (
        process_alive,
        read_pid,
        resolve_daemon_log_file,
        resolve_daemon_pid_file,
    )

    config = load_config(endpoint.config_path)
    pid_file = resolve_daemon_pid_file(
        config,
        home_root=endpoint.home_root or None,
        data_root=endpoint.data_root or None,
    )
    log_file = resolve_daemon_log_file(
        config,
        home_root=endpoint.home_root or None,
        data_root=endpoint.data_root or None,
    )

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
        _unlink_owned(pid_file, existing_pid)

    process = _spawn_daemon(endpoint, pid_file, log_file)

    started = False
    try:
        result = _await_daemon_ready(
            endpoint,
            process,
            pid_file=pid_file,
            log_file=log_file,
        )
        started = bool(result.get("ok"))
        return result
    finally:
        if not started:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if read_pid(pid_file) == process.pid:
                _safe_unlink(pid_file)


def _spawn_daemon(
    endpoint: DaemonEndpoint, pid_file: Path, log_file: Path
) -> subprocess.Popen[bytes]:
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

    env = os.environ.copy()
    if endpoint.home_root:
        env["OPENMINION_HOME"] = endpoint.home_root
    if endpoint.data_root:
        env["OPENMINION_DATA_ROOT"] = endpoint.data_root
    with log_file.open("a", encoding="utf-8") as stream:
        return subprocess.Popen(  # noqa: S603
            command,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def _unlink_owned(path: Path, expected_pid: int) -> bool:
    """Remove the PID file only while it still names ``expected_pid``.

    Cleanup runs after a longer window than the verification, so the file can already
    describe a replacement daemon by the time it is removed. Removing another
    process's PID file is worse than leaving a stale one behind.
    """
    try:
        current = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if current != expected_pid:
        return False
    _safe_unlink(path)
    return True


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
