"""Daemon-owned supervision for blocking controlplane channel adapters."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
import time
from typing import Any

from openminion.modules.controlplane import InboxWorker, OutboxWorker
from openminion.modules.telemetry.schemas import TelemetryEvent


@dataclass(frozen=True)
class ChannelStatus:
    channel_id: str
    configured: bool
    enabled: bool
    state: str
    mode: str = "unknown"
    listener_alive: bool = False
    connected: bool | None = None
    last_started_at: float | None = None
    last_stopped_at: float | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "configured": self.configured,
            "enabled": self.enabled,
            "state": self.state,
            "mode": self.mode,
            "listener_alive": self.listener_alive,
            "connected": self.connected,
            "last_started_at": self.last_started_at,
            "last_stopped_at": self.last_stopped_at,
            "last_error": self.last_error,
        }


@dataclass(frozen=True)
class ChannelRuntimeStatus:
    state: str
    channels: dict[str, ChannelStatus]
    inbox_worker_alive: bool
    outbox_worker_alive: bool
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "channels": {
                channel_id: status.to_dict()
                for channel_id, status in sorted(self.channels.items())
            },
            "inbox_worker_alive": self.inbox_worker_alive,
            "outbox_worker_alive": self.outbox_worker_alive,
            "last_error": self.last_error,
        }


@dataclass
class _ChannelRuntime:
    adapter: Any
    thread: threading.Thread | None = None
    state: str = "stopped"
    last_started_at: float | None = None
    last_stopped_at: float | None = None
    last_error: str | None = None


class ChannelRuntimeSupervisor:
    def __init__(
        self,
        *,
        channels: Any,
        inbox_worker: InboxWorker | None = None,
        outbox_worker: OutboxWorker | None = None,
        close_runtime: Any | None = None,
        telemetry_service: Any | None = None,
        logger: logging.Logger | None = None,
        channel_ids: list[str] | None = None,
    ) -> None:
        self._channels = channels
        self._inbox_worker = inbox_worker
        self._outbox_worker = outbox_worker
        self._close_runtime = close_runtime
        self._telemetry_service = telemetry_service
        self._log = logger or logging.getLogger(__name__)
        self._stop_event = threading.Event()
        self._inbox_thread: threading.Thread | None = None
        self._outbox_thread: threading.Thread | None = None
        self._runtimes: dict[str, _ChannelRuntime] = {}
        self._last_error: str | None = None
        self._runtime_closed = False
        for channel_id in channel_ids or _non_console_channel_ids(channels):
            adapter = channels.get(channel_id)
            self._runtimes[channel_id] = _ChannelRuntime(adapter=adapter)
            setattr(adapter, "_outbox_managed_by_supervisor", True)

    def bind_telemetry_service(self, telemetry_service: Any | None) -> None:
        self._telemetry_service = telemetry_service

    def start(self) -> dict[str, object]:
        self._stop_event.clear()
        self._start_inbox_worker()
        self._start_outbox_worker()
        results: dict[str, object] = {}
        for channel_id, runtime in self._runtimes.items():
            if runtime.thread is not None and runtime.thread.is_alive():
                results[channel_id] = {"ok": True, "state": runtime.state}
                continue
            runtime.state = "starting"
            runtime.last_started_at = time.time()
            thread = threading.Thread(
                target=self._run_adapter,
                args=(channel_id, runtime),
                daemon=True,
                name=f"controlplane-channel-{channel_id}",
            )
            runtime.thread = thread
            thread.start()
            runtime.state = "running"
            self._emit("controlplane.channel.started", channel_id=channel_id)
            results[channel_id] = {"ok": True, "state": runtime.state}
        return results

    def stop(self, *, timeout_seconds: float = 5.0) -> dict[str, object]:
        self._stop_event.set()
        results: dict[str, object] = {}
        for channel_id, runtime in self._runtimes.items():
            runtime.state = "stopping"
            stop = getattr(runtime.adapter, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception as exc:  # noqa: BLE001
                    runtime.last_error = _redact(str(exc))
                    self._last_error = runtime.last_error
            thread = runtime.thread
            if thread is not None:
                thread.join(timeout=timeout_seconds)
            alive = bool(thread and thread.is_alive())
            runtime.state = "degraded" if alive else "stopped"
            runtime.last_stopped_at = time.time()
            if alive:
                runtime.last_error = "join_timeout"
                self._last_error = "join_timeout"
                self._emit("controlplane.channel.join_timeout", channel_id=channel_id)
                self._emit(
                    "controlplane.channel.degraded",
                    channel_id=channel_id,
                    reason="join_timeout",
                )
            else:
                self._emit("controlplane.channel.stopped", channel_id=channel_id)
            results[channel_id] = {"ok": not alive, "state": runtime.state}
        self._stop_inbox_worker(timeout_seconds=timeout_seconds)
        self._stop_outbox_worker(timeout_seconds=timeout_seconds)
        self._close_shared_runtime()
        return results

    def status(self) -> ChannelRuntimeStatus:
        channel_statuses = {
            channel_id: self._channel_status(channel_id, runtime)
            for channel_id, runtime in self._runtimes.items()
        }
        states = {status.state for status in channel_statuses.values()}
        if not channel_statuses:
            state = "stopped"
        elif "failed" in states or "degraded" in states:
            state = "degraded"
        elif states == {"running"}:
            state = "running"
        elif "running" in states:
            state = "degraded"
        else:
            state = "stopped"
        return ChannelRuntimeStatus(
            state=state,
            channels=channel_statuses,
            inbox_worker_alive=bool(
                self._inbox_thread is not None and self._inbox_thread.is_alive()
            ),
            outbox_worker_alive=bool(
                self._outbox_thread is not None and self._outbox_thread.is_alive()
            ),
            last_error=self._last_error,
        )

    def _run_adapter(self, channel_id: str, runtime: _ChannelRuntime) -> None:
        try:
            runtime.adapter.start(stop_event=self._stop_event)
            if not self._stop_event.is_set():
                runtime.state = "stopped"
        except Exception as exc:  # noqa: BLE001
            runtime.last_error = _redact(str(exc))
            runtime.state = "failed"
            self._last_error = runtime.last_error
            self._log.warning(
                "controlplane channel failed channel=%s error=%s",
                channel_id,
                runtime.last_error,
                exc_info=True,
            )
            self._emit(
                "controlplane.channel.failed",
                channel_id=channel_id,
                error=runtime.last_error,
            )
            self._emit(
                "controlplane.channel.degraded",
                channel_id=channel_id,
                reason="startup_failed",
                error=runtime.last_error,
            )

    def _start_inbox_worker(self) -> None:
        if self._inbox_worker is None:
            return
        if self._inbox_thread is not None and self._inbox_thread.is_alive():
            return
        thread = threading.Thread(
            target=self._run_inbox_loop,
            daemon=True,
            name="controlplane-inbox-worker",
        )
        self._inbox_thread = thread
        thread.start()
        self._emit("controlplane.inbox_worker.started")

    def _start_outbox_worker(self) -> None:
        if self._outbox_worker is None:
            return
        if self._outbox_thread is not None and self._outbox_thread.is_alive():
            return
        thread = threading.Thread(
            target=self._run_outbox_loop,
            daemon=True,
            name="controlplane-outbox-worker",
        )
        self._outbox_thread = thread
        thread.start()
        self._emit("controlplane.outbox_worker.started")

    def _stop_inbox_worker(self, *, timeout_seconds: float) -> None:
        thread = self._inbox_thread
        if thread is None:
            return
        thread.join(timeout=timeout_seconds)
        if thread.is_alive():
            self._last_error = "inbox_join_timeout"
            self._emit("controlplane.inbox_worker.join_timeout")
            return
        self._inbox_thread = None
        self._emit("controlplane.inbox_worker.stopped")

    def _stop_outbox_worker(self, *, timeout_seconds: float) -> None:
        thread = self._outbox_thread
        if thread is None:
            return
        thread.join(timeout=timeout_seconds)
        if thread.is_alive():
            self._last_error = "outbox_join_timeout"
            self._emit("controlplane.outbox_worker.join_timeout")
            return
        self._outbox_thread = None
        self._emit("controlplane.outbox_worker.stopped")

    def _close_shared_runtime(self) -> None:
        if self._runtime_closed:
            return
        if not callable(self._close_runtime):
            return
        try:
            self._close_runtime()
            self._runtime_closed = True
        except Exception as exc:  # noqa: BLE001
            self._last_error = _redact(str(exc))
            self._emit("controlplane.runtime.close_failed", error=self._last_error)

    def _run_inbox_loop(self) -> None:
        while True:
            try:
                result = self._inbox_worker.run_once()  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001
                self._last_error = _redact(str(exc))
                self._log.warning(
                    "controlplane inbox worker failed: %s",
                    exc,
                    exc_info=True,
                )
                self._emit("controlplane.inbox_worker.failed", error=self._last_error)
                time.sleep(0.1)
                continue
            if result is None:
                if self._stop_event.is_set():
                    return
                time.sleep(0.1)

    def _run_outbox_loop(self) -> None:
        while True:
            try:
                result = self._outbox_worker.run_once()  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001
                self._last_error = _redact(str(exc))
                self._log.warning(
                    "controlplane outbox worker failed: %s",
                    exc,
                    exc_info=True,
                )
                self._emit("controlplane.outbox_worker.failed", error=self._last_error)
                time.sleep(0.1)
                continue
            if result is None:
                if self._stop_event.is_set():
                    return
                time.sleep(0.1)

    def _channel_status(self, channel_id: str, runtime: _ChannelRuntime) -> ChannelStatus:
        health = _adapter_health(runtime.adapter)
        thread_alive = bool(runtime.thread is not None and runtime.thread.is_alive())
        state = runtime.state
        if state == "running" and not thread_alive:
            state = "failed" if runtime.last_error else "stopped"
        return ChannelStatus(
            channel_id=channel_id,
            configured=True,
            enabled=True,
            state=state,
            mode=str(
                health.get("mode")
                or getattr(getattr(runtime.adapter, "_config", None), "mode", "unknown")
            ),
            listener_alive=thread_alive,
            connected=health.get("connected") if "connected" in health else health.get("ok"),
            last_started_at=runtime.last_started_at,
            last_stopped_at=runtime.last_stopped_at,
            last_error=runtime.last_error or _redact(str(health.get("error") or "")) or None,
        )

    def _emit(self, event_type: str, **payload: object) -> None:
        recorder = getattr(self._telemetry_service, "record_event_sync", None)
        if not callable(recorder):
            return
        recorder(
            TelemetryEvent(
                session_id="controlplane:channels",
                turn_id=event_type,
                event_type=event_type,
                data={key: value for key, value in payload.items() if value is not None},
            )
        )


def _non_console_channel_ids(channels: Any) -> list[str]:
    names = channels.names() if hasattr(channels, "names") else []
    return [str(name) for name in names if str(name) != "console"]


def _adapter_health(adapter: Any) -> dict[str, Any]:
    probe = getattr(adapter, "health", None)
    if not callable(probe):
        return {"ok": None}
    try:
        value = probe()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": _redact(str(exc))}
    return dict(value) if isinstance(value, dict) else {"ok": bool(value)}


def _redact(message: str) -> str:
    text = str(message or "")
    for marker in ("xoxb-", "xapp-", "bot"):
        if marker in text:
            return "<redacted>"
    return text
