import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable, Mapping, Sequence

from openminion.base.config.env.subprocess import build_subprocess_env

from .client import parse_base_url_targets

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class PinchTabDaemonConfig:
    base_url: str
    runtime_dir: Path
    launch_cmd: tuple[str, ...]
    launch_timeout_s: int
    env: Mapping[str, str]

    @property
    def pid_file(self) -> Path:
        return self.runtime_dir / "pinchtab.pid"

    @property
    def log_file(self) -> Path:
        return self.runtime_dir / "pinchtab.log"


def _normalize_launch_cmd(
    raw: str | Sequence[str] | None, *, port: int
) -> tuple[str, ...]:
    if raw:
        if isinstance(raw, str):
            tokens = shlex.split(raw)
        else:
            tokens = [str(item) for item in raw if str(item).strip()]
        if tokens:
            return tuple(token.format(port=port) for token in tokens)
    pinchtab_bin = shutil.which("pinchtab")
    if pinchtab_bin:
        return (pinchtab_bin, "serve", "--port", str(port))
    return (sys.executable, "-m", "pinchtab", "serve", "--port", str(port))


def _read_pid(pid_file: Path) -> int | None:
    try:
        raw = pid_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        pid = int(raw)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def daemon_status(cfg: PinchTabDaemonConfig) -> dict[str, object]:
    pid = _read_pid(cfg.pid_file)
    alive = bool(pid and _process_alive(pid))
    return {
        "base_url": cfg.base_url,
        "pid": pid or 0,
        "pid_alive": alive,
        "pid_file": str(cfg.pid_file),
        "log_file": str(cfg.log_file),
        "runtime_dir": str(cfg.runtime_dir),
        "launch_cmd": list(cfg.launch_cmd),
    }


def build_daemon_config(
    *,
    base_url: str,
    runtime_dir: Path,
    launch_cmd: str | Sequence[str] | None = None,
    launch_timeout_s: int = 20,
    env: Mapping[str, str] | None = None,
) -> PinchTabDaemonConfig:
    host, port, _ = parse_base_url_targets(base_url)
    if host and host not in _LOCAL_HOSTS:
        raise ValueError("PinchTab autostart requires a localhost base_url")
    resolved_cmd = _normalize_launch_cmd(launch_cmd, port=port)
    runtime_dir = runtime_dir.expanduser().resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return PinchTabDaemonConfig(
        base_url=base_url,
        runtime_dir=runtime_dir,
        launch_cmd=resolved_cmd,
        launch_timeout_s=max(1, int(launch_timeout_s)),
        env=dict(env or {}),
    )


def start_daemon(cfg: PinchTabDaemonConfig) -> dict[str, object]:
    status = daemon_status(cfg)
    if status["pid_alive"]:
        return {"started": False, "status": status}
    pid = status["pid"]
    if pid and not status["pid_alive"]:
        cfg.pid_file.unlink(missing_ok=True)
    cfg.runtime_dir.mkdir(parents=True, exist_ok=True)
    env = build_subprocess_env(overlay=cfg.env)
    log_handle = cfg.log_file.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(  # noqa: S603
            list(cfg.launch_cmd),
            cwd=str(cfg.runtime_dir),
            env=env,
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
        )
    finally:
        log_handle.close()
    cfg.pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    return {"started": True, "pid": process.pid, "status": daemon_status(cfg)}


def wait_for_ready(check_fn: Callable[[], object], *, timeout_s: int) -> bool:
    deadline = time.monotonic() + max(1, int(timeout_s))
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            check_fn()
            return True
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.25)
    if last_error is not None:
        raise last_error
    return False


def ensure_daemon(
    cfg: PinchTabDaemonConfig,
    *,
    check_fn: Callable[[], object] | None = None,
) -> dict[str, object]:
    status = daemon_status(cfg)
    if status["pid_alive"]:
        return {"started": False, "status": status}
    started = start_daemon(cfg)
    if check_fn is not None:
        wait_for_ready(check_fn, timeout_s=cfg.launch_timeout_s)
    return started


def stop_daemon(
    cfg: PinchTabDaemonConfig, *, kill: bool = False, timeout_s: int = 3
) -> dict[str, object]:
    status = daemon_status(cfg)
    pid = int(status["pid"])
    if not pid:
        return {"stopped": False, "status": status, "reason": "no_pid"}
    if not status["pid_alive"]:
        cfg.pid_file.unlink(missing_ok=True)
        return {"stopped": True, "status": daemon_status(cfg), "reason": "stale_pid"}
    sig = signal.SIGKILL if kill else signal.SIGTERM
    try:
        os.kill(pid, sig)
    except OSError as exc:
        return {"stopped": False, "status": status, "error": str(exc)}
    deadline = time.monotonic() + max(1, int(timeout_s))
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            cfg.pid_file.unlink(missing_ok=True)
            return {"stopped": True, "status": daemon_status(cfg)}
        time.sleep(0.2)
    return {"stopped": False, "status": daemon_status(cfg), "reason": "timeout"}
