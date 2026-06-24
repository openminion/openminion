from __future__ import annotations

import argparse
import os
import signal
import threading
import time
from pathlib import Path
from types import FrameType
from typing import Optional, Sequence, cast

from openminion.api.server import build_api_server
from openminion.base.config import (
    OpenMinionConfig,
    resolve_data_root,
    resolve_home_root,
    EnvironmentConfig,
    OTELExporterConfig,
)
from openminion.base.config import ConfigManager
from openminion.base.logging import (
    configure_logging,
    format_structured_event,
    get_logger,
)
from openminion.modules.telemetry.lifecycle import (
    build_component_identity,
    build_lifecycle_telemetry_event,
)
from openminion.modules.telemetry.service import TelemetryService
from openminion.services.bootstrap.config import bootstrap_config_manager
from openminion.services.runtime.daemon import attach_cron_scheduler
from openminion.services.supervision import SupervisionPolicy

logger = get_logger("daemon")
_DAEMON_LIFECYCLE_SESSION_ID = "lifecycle:daemon:primary"
_DAEMON_HEARTBEAT_INTERVAL_SECONDS = 15.0
_DAEMON_STALE_HEARTBEAT_WARN_MULTIPLIER = 2.0
_DAEMON_STALE_HEARTBEAT_FAIL_MULTIPLIER = 4.0


class _DaemonLifecycleEmitter:
    def __init__(
        self,
        *,
        home_root: Path,
        env: dict[str, str] | None,
        otel_exporter_config: object | None = None,
        pid: int,
        bind_host: str,
        bind_port: int,
        telemetry_service: TelemetryService | None = None,
        heartbeat_interval_seconds: float = _DAEMON_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self._pid = max(0, int(pid))
        self._bind_host = str(bind_host or "").strip() or "127.0.0.1"
        self._bind_port = max(1, int(bind_port))
        self._logger = logger.getChild("lifecycle")
        self._telemetry: TelemetryService | None
        self._started_monotonic = time.monotonic()
        self._sequence = 0
        self._heartbeat_interval_seconds = max(1.0, float(heartbeat_interval_seconds))
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_stop = threading.Event()
        self._component = build_component_identity(
            component_kind="daemon",
            component_id="primary",
            scope="system",
            owner_module="openminion-runtime",
            capabilities=["http", "scheduler", "heartbeat"],
            labels={
                "topology": "daemon-hosted",
                "bind_host": self._bind_host,
            },
        )
        try:
            self._telemetry = telemetry_service or TelemetryService(
                home_root=home_root,
                env=env,
                otel_exporter_config=cast(
                    OTELExporterConfig | None, otel_exporter_config
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._telemetry = None
            self._logger.warning(
                format_structured_event(
                    "daemon.lifecycle.telemetry_unavailable",
                    error=exc,
                )
            )

    def emit_started(self) -> None:
        self._record(
            event_type="component.started",
            status="ok",
            reason="process_boot",
            metrics=self._metrics(),
            evidence=self._bind_evidence(),
        )

    def emit_heartbeat(self) -> None:
        self._record(
            event_type="component.heartbeat",
            status="ok",
            reason="heartbeat",
            metrics=self._metrics(include_uptime=True),
        )

    def emit_stopped(self, *, reason: str) -> None:
        self._record(
            event_type="component.stopped",
            status="ok",
            reason=reason,
            metrics=self._metrics(include_uptime=True),
        )

    def emit_crashed(self, *, reason: str, error: Exception | None = None) -> None:
        evidence = self._bind_evidence()
        if error is not None:
            evidence["error_class"] = error.__class__.__name__
            message = str(error or "").strip()
            if message:
                evidence["error_message"] = message[:200]
        self._record(
            event_type="component.crashed",
            status="error",
            reason=reason,
            metrics=self._metrics(include_uptime=True),
            evidence=evidence,
        )

    def start_heartbeat(self, *, stop_event: threading.Event) -> None:
        if self._heartbeat_thread is not None:
            return

        def _loop() -> None:
            while True:
                if stop_event.is_set() or self._heartbeat_stop.wait(
                    self._heartbeat_interval_seconds
                ):
                    return
                self.emit_heartbeat()

        self._heartbeat_thread = threading.Thread(
            target=_loop,
            name="openminiond-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=1.0)
            self._heartbeat_thread = None

    def close(self) -> None:
        self.stop_heartbeat()
        if self._telemetry is None:
            return
        try:
            self._telemetry.close_sync()
        except Exception:
            return

    def _record(
        self,
        *,
        event_type: str,
        status: str,
        reason: str,
        metrics: dict[str, object] | None = None,
        evidence: dict[str, object] | None = None,
    ) -> None:
        if self._telemetry is None:
            return
        self._sequence += 1
        try:
            event = build_lifecycle_telemetry_event(
                event_type=event_type,
                component=self._component,
                module_id="openminion-runtime",
                session_id=_DAEMON_LIFECYCLE_SESSION_ID,
                turn_id=f"daemon:{event_type.rsplit('.', 1)[-1]}:{self._sequence}",
                status=status,
                reason=reason,
                metrics=metrics,
                evidence=evidence,
                source_classification="native_canonical",
            )
            self._telemetry.record_event_sync(event)
            self._logger.info(
                format_structured_event(
                    event_type,
                    reason=reason,
                    pid=self._pid,
                )
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                format_structured_event(
                    "daemon.lifecycle.emit_failed",
                    source_event=event_type,
                    error=exc,
                )
            )

    def _uptime_seconds(self) -> int:
        return max(0, int(time.monotonic() - self._started_monotonic))

    def _metrics(self, *, include_uptime: bool = False) -> dict[str, object]:
        metrics: dict[str, object] = {"pid": self._pid, "port": self._bind_port}
        if include_uptime:
            metrics["uptime_seconds"] = self._uptime_seconds()
        return metrics

    def _bind_evidence(self) -> dict[str, object]:
        return {
            "bind_host": self._bind_host,
            "bind_port": self._bind_port,
        }


def build_daemon_supervision_policy(
    *,
    heartbeat_interval_seconds: float = _DAEMON_HEARTBEAT_INTERVAL_SECONDS,
    stale_heartbeat_warn_after_seconds: float | None = None,
    stale_heartbeat_fail_after_seconds: float | None = None,
    restart_enabled: bool = False,
    restart_max_attempts: int = 0,
    restart_initial_backoff_seconds: float = 5.0,
    restart_max_backoff_seconds: float = 300.0,
    crash_loop_threshold: int = 3,
) -> SupervisionPolicy:
    interval = max(1.0, float(heartbeat_interval_seconds))
    warn_after = stale_heartbeat_warn_after_seconds
    if warn_after is None:
        warn_after = interval * _DAEMON_STALE_HEARTBEAT_WARN_MULTIPLIER
    fail_after = stale_heartbeat_fail_after_seconds
    if fail_after is None:
        fail_after = interval * _DAEMON_STALE_HEARTBEAT_FAIL_MULTIPLIER
    return SupervisionPolicy(
        stale_heartbeat_warn_after_seconds=warn_after,
        stale_heartbeat_fail_after_seconds=fail_after,
        restart_enabled=restart_enabled,
        restart_max_attempts=restart_max_attempts,
        restart_initial_backoff_seconds=restart_initial_backoff_seconds,
        restart_max_backoff_seconds=restart_max_backoff_seconds,
        crash_loop_threshold=crash_loop_threshold,
    )


def _resolve_daemon_file(
    config: OpenMinionConfig, configured_path: Optional[str], default_relative: str
) -> Path:
    configured = str(configured_path or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    env_config = EnvironmentConfig.from_sources(
        runtime_env=getattr(config.runtime, "env", None),
    )
    env_values = env_config.snapshot()
    home_root = resolve_home_root(env=env_values)
    data_root = resolve_data_root(
        home_root,
        data_root=env_config.openminion_data_root or None,
        env=env_values,
    )
    return (data_root / default_relative).resolve()


def resolve_daemon_pid_file(config: OpenMinionConfig) -> Path:
    return _resolve_daemon_file(
        config, config.runtime.daemon_pid_file, "state/openminiond.pid"
    )


def resolve_daemon_log_file(config: OpenMinionConfig) -> Path:
    configured = getattr(getattr(config, "runtime", object()), "daemon_log_file", "")
    return _resolve_daemon_file(config, configured, "logs/openminiond.log")


def resolve_ipc_bind(config: OpenMinionConfig) -> tuple[str, int]:
    host = str(config.runtime.ipc_host or config.gateway.host).strip() or "127.0.0.1"
    try:
        port = int(config.runtime.ipc_port or config.gateway.port)
    except (TypeError, ValueError):
        port = int(config.gateway.port)
    return host, max(1, port)


def read_pid(pid_file: Path) -> Optional[int]:
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


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def run_server(
    *,
    config_path: Optional[str],
    host: Optional[str] = None,
    port: Optional[int] = None,
    pid_file: Optional[str] = None,
) -> int:
    manager = ConfigManager.load(config_path)
    bootstrap_config_manager(manager)
    resolved_config_path = manager.config_path
    config = manager.base_config
    bind_host, bind_port = resolve_ipc_bind(config)
    if host is not None:
        bind_host = str(host).strip() or bind_host
    if port is not None:
        bind_port = int(port)

    resolved_pid_file = (
        Path(pid_file).expanduser().resolve()
        if pid_file
        else resolve_daemon_pid_file(config)
    )
    resolved_pid_file.parent.mkdir(parents=True, exist_ok=True)
    resolved_log_file = resolve_daemon_log_file(config)
    resolved_log_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_log_level = str(
        getattr(getattr(config, "runtime", object()), "log_level", "INFO") or "INFO"
    )
    configure_logging(
        runtime_log_level,
        mode="daemon",
        file_path=resolved_log_file,
    )

    server = build_api_server(
        config_path=str(resolved_config_path), host=bind_host, port=bind_port
    )
    pid = os.getpid()
    resolved_pid_file.write_text(f"{pid}\n", encoding="utf-8")
    lifecycle = _DaemonLifecycleEmitter(
        home_root=manager.home_root,
        env=getattr(config.runtime, "env", None),
        otel_exporter_config=getattr(config.runtime, "telemetry_exporter", None),
        pid=pid,
        bind_host=bind_host,
        bind_port=bind_port,
    )

    if server._runtime is None:
        raise RuntimeError("API server runtime was not initialized.")
    scheduler = attach_cron_scheduler(
        runtime=server._runtime,
        daemon_id=f"daemon-{pid}",
        daemon_component_id="primary",
        daemon_pid=pid,
        tick_seconds=0.5,
        lease_ttl_seconds=60,
        max_concurrent_runs=5,
    )

    _stop_event = threading.Event()
    _shutdown_started = threading.Event()
    shutdown_reason = "server_stop"
    crashed = False

    def _graceful_shutdown() -> None:
        # socketserver.BaseServer.shutdown() must be called from a different
        # thread than serve_forever() to avoid deadlock.
        if scheduler:
            try:
                scheduler.shutdown(grace_s=3)
            except Exception as exc:
                logger.warning(
                    format_structured_event(
                        "daemon.scheduler.shutdown_failed",
                        error=exc,
                    )
                )
        try:
            server.shutdown()
        except Exception as exc:
            logger.warning(
                format_structured_event(
                    "daemon.server.shutdown_failed",
                    error=exc,
                )
            )

    def _handle_signal(signum: int, _frame: FrameType | None) -> None:
        nonlocal shutdown_reason
        if _stop_event.is_set() or _shutdown_started.is_set():
            return
        _stop_event.set()
        _shutdown_started.set()
        shutdown_reason = "signal_stop"
        thread = threading.Thread(
            target=_graceful_shutdown,
            name="openminiond-shutdown",
            daemon=True,
        )
        thread.start()

    previous_handlers: list[tuple[signal.Signals, object]] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous_handlers.append((sig, signal.getsignal(sig)))
        signal.signal(sig, _handle_signal)

    exit_code = 0
    try:
        lifecycle.emit_started()
        lifecycle.emit_heartbeat()
        lifecycle.start_heartbeat(stop_event=_stop_event)
        server.serve_forever(poll_interval=0.2)
    except Exception as exc:
        logger.error(
            format_structured_event("daemon.server.exited_error", error=exc),
            exc_info=True,
        )
        exit_code = 1
        crashed = True
        lifecycle.emit_crashed(reason="server_error", error=exc)
    finally:
        _stop_event.set()
        lifecycle.stop_heartbeat()
        try:
            server.server_close()
        except Exception as exc:
            logger.warning(
                format_structured_event(
                    "daemon.server.close_failed",
                    error=exc,
                )
            )
            exit_code = 1
            if not crashed:
                crashed = True
                lifecycle.emit_crashed(reason="server_close_error", error=exc)
        for sig, previous in previous_handlers:
            signal.signal(sig, cast(signal.Handlers | int | None, previous))
        if not crashed and exit_code == 0:
            lifecycle.emit_stopped(reason=shutdown_reason)
        try:
            current_pid = read_pid(resolved_pid_file)
            if current_pid == pid:
                resolved_pid_file.unlink(missing_ok=True)
        except OSError:
            pass
        lifecycle.close()
    return exit_code


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="openminion.daemon")
    subparsers = parser.add_subparsers(dest="command")

    serve_cmd = subparsers.add_parser("serve", help="Run openminiond foreground server")
    serve_cmd.add_argument("--config", default=None, help="Config path")
    serve_cmd.add_argument("--host", default=None, help="Bind host")
    serve_cmd.add_argument("--port", type=int, default=None, help="Bind port")
    serve_cmd.add_argument("--pid-file", default=None, help="PID file path")

    args = parser.parse_args(argv)
    if args.command != "serve":
        parser.print_help()
        return 1
    return run_server(
        config_path=args.config,
        host=args.host,
        port=args.port,
        pid_file=args.pid_file,
    )


if __name__ == "__main__":
    raise SystemExit(main())
