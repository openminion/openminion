from typing import Any, Protocol, runtime_checkable
from collections.abc import Mapping


SQL_LOG_PREFIX_CHARS = 200


def redact_sql(sql: str) -> str:
    """Return the redacted, hook-loggable form of ``sql``."""

    if len(sql) <= SQL_LOG_PREFIX_CHARS:
        return sql
    return sql[:SQL_LOG_PREFIX_CHARS] + "..."


@runtime_checkable
class StorageTelemetryHook(Protocol):
    """Hook surface storage backends call to emit telemetry events."""

    def on_pool_stats(self, stats: Mapping[str, Any]) -> None:
        """Receive a periodic pool-health snapshot."""
        ...

    def on_query_start(self, sql: str, params: Any) -> Any:
        """Called immediately before a query/statement runs."""
        ...

    def on_query_end(
        self,
        token: Any,
        duration_ms: float,
        error: str | None,
    ) -> None:
        """Called after a query/statement finishes (success or failure)."""
        ...

    def on_error_class(
        self,
        *,
        error_class: str,
        operation: str,
        error: str,
    ) -> None:
        """Called when storage classifies an operation failure."""
        ...

    def on_slow_query(
        self,
        sql: str,
        duration_ms: float,
        threshold_ms: int,
    ) -> None:
        """Called when a query's ``duration_ms`` exceeds ``threshold_ms``.

        Always emitted *after* ``on_query_end``; callers do not need to
        deduplicate. ``sql`` is the (already-redacted) SQL prefix.
        """
        ...

    def on_migration_start(self, module_id: str, operation: str) -> Any:
        """Called immediately before a storage migration operation starts."""
        ...

    def on_migration_end(
        self,
        token: Any,
        module_id: str,
        operation: str,
        duration_ms: float,
        success: bool,
        error: str | None,
    ) -> None:
        """Called after a storage migration operation finishes."""
        ...


class NoopStorageTelemetryHook:
    """Default no-op `StorageTelemetryHook` implementation.

    Used when no telemetry adapter is wired (e.g. CLI tools, standalone
    storage tests). All Protocol methods are silent no-ops.
    """

    def on_pool_stats(self, stats: Mapping[str, Any]) -> None:
        del stats
        return None

    def on_query_start(self, sql: str, params: Any) -> Any:
        del sql, params
        return None

    def on_query_end(
        self,
        token: Any,
        duration_ms: float,
        error: str | None,
    ) -> None:
        del token, duration_ms, error
        return None

    def on_error_class(
        self,
        *,
        error_class: str,
        operation: str,
        error: str,
    ) -> None:
        del error_class, operation, error
        return None

    def on_slow_query(
        self,
        sql: str,
        duration_ms: float,
        threshold_ms: int,
    ) -> None:
        del sql, duration_ms, threshold_ms
        return None

    def on_migration_start(self, module_id: str, operation: str) -> Any:
        del module_id, operation
        return None

    def on_migration_end(
        self,
        token: Any,
        module_id: str,
        operation: str,
        duration_ms: float,
        success: bool,
        error: str | None,
    ) -> None:
        del token, module_id, operation, duration_ms, success, error
        return None


__all__ = [
    "StorageTelemetryHook",
    "NoopStorageTelemetryHook",
    "SQL_LOG_PREFIX_CHARS",
    "redact_sql",
]
