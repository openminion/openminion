from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from openminion.modules.storage.config import (
    POOL_HEALTH_EMIT_INTERVAL_SECONDS_DEFAULT,
)
from openminion.modules.storage.telemetry import StorageTelemetryHook

if TYPE_CHECKING:
    from openminion.modules.storage.record_store import RecordStore


_LOG = logging.getLogger(__name__)

# Re-exported for backward-compatible imports; canonical owner is
# `openminion.modules.storage.config`.
DEFAULT_INTERVAL_SECONDS = POOL_HEALTH_EMIT_INTERVAL_SECONDS_DEFAULT


class PoolHealthEmitter:
    """Background-thread emitter for periodic pool-health snapshots."""

    def __init__(
        self,
        record_store: "RecordStore",
        hook: StorageTelemetryHook,
        *,
        interval_seconds: float = POOL_HEALTH_EMIT_INTERVAL_SECONDS_DEFAULT,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError(f"interval_seconds must be > 0; got {interval_seconds!r}")
        self._record_store = record_store
        self._hook = hook
        self._interval_seconds = float(interval_seconds)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def interval_seconds(self) -> float:
        return self._interval_seconds

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """Spawn the background emission thread (idempotent)."""

        if self.is_running:
            return
        self._stop_event.clear()
        thread = threading.Thread(
            target=self._run,
            name="storage-pool-health-emitter",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def stop(self, *, timeout: float | None = None) -> None:
        """Signal shutdown and join the background thread.

        ``timeout`` defaults to ``interval_seconds + 1.0`` to give the
        loop a full cycle to observe the stop signal.
        """

        thread = self._thread
        if thread is None:
            return
        self._stop_event.set()
        thread.join(
            timeout=(self._interval_seconds + 1.0) if timeout is None else timeout,
        )
        self._thread = None

    def emit_once(self) -> bool:
        """Take one pool-health snapshot and deliver to the hook."""

        try:
            stats = self._record_store.pool_health()
        except Exception:  # noqa: BLE001
            _LOG.warning(
                "pool_health() raised; skipping emission for this tick",
                exc_info=True,
            )
            return False
        if stats is None:
            return False
        try:
            self._hook.on_pool_stats(stats)
        except Exception:  # noqa: BLE001
            _LOG.warning(
                "StorageTelemetryHook.on_pool_stats raised; swallowing",
                exc_info=True,
            )
        return True

    def _run(self) -> None:
        # Emit immediately on start so operators see the first stats
        # without waiting for the first interval to elapse.
        self.emit_once()
        while not self._stop_event.wait(self._interval_seconds):
            self.emit_once()


__all__ = ["PoolHealthEmitter", "DEFAULT_INTERVAL_SECONDS"]
