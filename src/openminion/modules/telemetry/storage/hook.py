from __future__ import annotations

import logging
import time
from typing import Any, Mapping

from openminion.modules.storage.telemetry import StorageTelemetryHook
from openminion.modules.telemetry.events.catalog import (
    STORAGE_ERROR_CLASS,
    STORAGE_MIGRATION,
    STORAGE_POOL_STATS,
    STORAGE_QUERY,
    STORAGE_SLOW_QUERY,
)
from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.service import TelemetryService

_LOG = logging.getLogger(__name__)


POOL_STATS_EVENT_TYPE = STORAGE_POOL_STATS
QUERY_EVENT_TYPE = STORAGE_QUERY
SLOW_QUERY_EVENT_TYPE = STORAGE_SLOW_QUERY
MIGRATION_EVENT_TYPE = STORAGE_MIGRATION
ERROR_CLASS_EVENT_TYPE = STORAGE_ERROR_CLASS
_POOL_STATS_SESSION_ID = "storage"
_POOL_STATS_TURN_ID = "pool.stats"
_QUERY_SESSION_ID = "storage"
_QUERY_TURN_ID = "query"
_MIGRATION_SESSION_ID = "storage"


class TelemetryServiceStorageHook(StorageTelemetryHook):
    """Bridge ``StorageTelemetryHook`` Protocol calls into ``TelemetryService``."""

    def __init__(self, telemetry_service: TelemetryService) -> None:
        self._telemetry_service = telemetry_service

    @property
    def telemetry_service(self) -> TelemetryService:
        return self._telemetry_service

    def _record_event_safely(self, event: TelemetryEvent) -> None:
        try:
            self._telemetry_service.record_event_sync(event)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning(
                "storage telemetry emit failed for event_type=%s: %s",
                event.event_type,
                exc,
            )

    def on_pool_stats(self, stats: Mapping[str, Any]) -> None:
        event = TelemetryEvent(
            session_id=_POOL_STATS_SESSION_ID,
            turn_id=_POOL_STATS_TURN_ID,
            event_type=POOL_STATS_EVENT_TYPE,
            timestamp=time.time(),
            data=dict(stats),
        )
        self._record_event_safely(event)

    def on_query_start(self, sql: str, params: Any) -> Any:
        del params  # PII; never logged
        return time.perf_counter()

    def on_query_end(self, token: Any, duration_ms: float, error: str | None) -> None:
        del token
        event = TelemetryEvent(
            session_id=_QUERY_SESSION_ID,
            turn_id=_QUERY_TURN_ID,
            event_type=QUERY_EVENT_TYPE,
            timestamp=time.time(),
            data={"duration_ms": float(duration_ms), "error": error},
        )
        self._record_event_safely(event)

    def on_error_class(
        self,
        *,
        error_class: str,
        operation: str,
        error: str,
    ) -> None:
        event = TelemetryEvent(
            session_id=_QUERY_SESSION_ID,
            turn_id=f"error.{operation}",
            event_type=ERROR_CLASS_EVENT_TYPE,
            timestamp=time.time(),
            data={
                "counter_name": "storage_error_class",
                "counter_value": 1.0,
                "error_class": str(error_class or "").strip() or "Exception",
                "operation": str(operation or "").strip() or "query",
                "error": str(error or ""),
            },
        )
        self._record_event_safely(event)

    def on_slow_query(self, sql: str, duration_ms: float, threshold_ms: int) -> None:
        event = TelemetryEvent(
            session_id=_QUERY_SESSION_ID,
            turn_id=_QUERY_TURN_ID,
            event_type=SLOW_QUERY_EVENT_TYPE,
            timestamp=time.time(),
            data={
                "sql": sql,
                "duration_ms": float(duration_ms),
                "threshold_ms": int(threshold_ms),
            },
        )
        self._record_event_safely(event)

    def on_migration_start(self, module_id: str, operation: str) -> Any:
        del module_id, operation
        return time.perf_counter()

    def on_migration_end(
        self,
        token: Any,
        module_id: str,
        operation: str,
        duration_ms: float,
        success: bool,
        error: str | None,
    ) -> None:
        del token
        event = TelemetryEvent(
            session_id=_MIGRATION_SESSION_ID,
            turn_id=f"migration.{module_id}.{operation}",
            event_type=MIGRATION_EVENT_TYPE,
            timestamp=time.time(),
            data={
                "module_id": module_id,
                "operation": operation,
                "duration_ms": float(duration_ms),
                "success": bool(success),
                "error": error,
            },
        )
        self._record_event_safely(event)


__all__ = [
    "TelemetryServiceStorageHook",
    "POOL_STATS_EVENT_TYPE",
    "QUERY_EVENT_TYPE",
    "SLOW_QUERY_EVENT_TYPE",
    "MIGRATION_EVENT_TYPE",
    "ERROR_CLASS_EVENT_TYPE",
]
