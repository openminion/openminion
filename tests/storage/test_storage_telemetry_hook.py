from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Mapping

import pytest

from openminion.modules.storage.record_store import RecordStoreSQLite
from openminion.modules.storage.runtime.health_emitter import (
    DEFAULT_INTERVAL_SECONDS,
    PoolHealthEmitter,
)
from openminion.modules.storage.telemetry import (
    NoopStorageTelemetryHook,
    SQL_LOG_PREFIX_CHARS,
    StorageTelemetryHook,
    redact_sql,
)
from openminion.modules.telemetry.storage.hook import (
    ERROR_CLASS_EVENT_TYPE,
    POOL_STATS_EVENT_TYPE,
    QUERY_EVENT_TYPE,
    SLOW_QUERY_EVENT_TYPE,
    TelemetryServiceStorageHook,
)


def test_protocol_callable() -> None:
    noop = NoopStorageTelemetryHook()
    assert isinstance(noop, StorageTelemetryHook)
    assert noop.on_pool_stats({"pool_size": 5, "backend": "postgres"}) is None
    assert noop.on_pool_stats({}) is None


class _RecordingHook:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def on_pool_stats(self, stats: Mapping[str, Any]) -> None:
        with self._lock:
            self.calls.append(dict(stats))


class _PostgresMockRecordStore:
    def __init__(self) -> None:
        self._counter = 0
        self._lock = threading.Lock()

    def pool_health(self) -> dict[str, Any] | None:
        with self._lock:
            self._counter += 1
            return {
                "pool_size": 5,
                "checked_out": self._counter,
                "overflow": 0,
                "oldest_connection_age_seconds": 1.0,
                "backend": "postgres",
            }


def test_emitter_lifecycle_emits_on_postgres_mock() -> None:
    hook = _RecordingHook()
    store = _PostgresMockRecordStore()
    emitter = PoolHealthEmitter(store, hook, interval_seconds=0.05)
    emitter.start()
    try:
        deadline = time.time() + 2.0
        while time.time() < deadline and len(hook.calls) < 2:
            time.sleep(0.02)
    finally:
        emitter.stop(timeout=2.0)
    assert len(hook.calls) >= 2, hook.calls
    sample = hook.calls[0]
    assert sample["backend"] == "postgres"
    assert sample["pool_size"] == 5
    assert not emitter.is_running


def test_emitter_noops_on_sqlite(tmp_path: Path) -> None:
    hook = _RecordingHook()
    sqlite_store = RecordStoreSQLite(str(tmp_path / "sths.db"))
    try:
        assert sqlite_store.pool_health() is None
        emitter = PoolHealthEmitter(sqlite_store, hook, interval_seconds=0.05)
        emitter.start()
        time.sleep(0.25)
        emitter.stop(timeout=1.0)
    finally:
        sqlite_store.close()
    assert hook.calls == []


def test_emitter_swallows_hook_exceptions() -> None:
    class _RaisingHook:
        def __init__(self) -> None:
            self.calls = 0

        def on_pool_stats(self, stats: Mapping[str, Any]) -> None:
            del stats
            self.calls += 1
            raise RuntimeError("synthetic telemetry sink failure")

    hook = _RaisingHook()
    store = _PostgresMockRecordStore()
    emitter = PoolHealthEmitter(store, hook, interval_seconds=0.05)
    assert emitter.emit_once() is True
    assert hook.calls == 1


def test_emitter_rejects_non_positive_interval() -> None:
    store = _PostgresMockRecordStore()
    hook = _RecordingHook()
    with pytest.raises(ValueError):
        PoolHealthEmitter(store, hook, interval_seconds=0.0)
    with pytest.raises(ValueError):
        PoolHealthEmitter(store, hook, interval_seconds=-1.0)


def test_emitter_default_interval_matches_constant() -> None:
    store = _PostgresMockRecordStore()
    hook = _RecordingHook()
    emitter = PoolHealthEmitter(store, hook)
    assert emitter.interval_seconds == DEFAULT_INTERVAL_SECONDS


class _RecordingTelemetryService:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def record_event_sync(self, event: Any) -> None:
        self.events.append(event)


def test_adapter_bridges_into_telemetry_service() -> None:
    service = _RecordingTelemetryService()
    adapter = TelemetryServiceStorageHook(service)
    stats = {
        "pool_size": 5,
        "checked_out": 2,
        "overflow": 0,
        "oldest_connection_age_seconds": 12.5,
        "backend": "postgres",
    }
    adapter.on_pool_stats(stats)
    assert len(service.events) == 1
    event = service.events[0]
    assert event.event_type == POOL_STATS_EVENT_TYPE
    assert event.data == stats
    stats["pool_size"] = 999
    assert service.events[0].data["pool_size"] == 5


def test_adapter_swallows_record_event_sync_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _FailingTelemetryService:
        def record_event_sync(self, event: Any) -> None:
            del event
            raise RuntimeError("synthetic storage telemetry failure")

    adapter = TelemetryServiceStorageHook(_FailingTelemetryService())

    with caplog.at_level("WARNING"):
        adapter.on_pool_stats({"backend": "postgres", "pool_size": 5})
        adapter.on_query_end(token=1, duration_ms=12.5, error=None)
        adapter.on_slow_query("SELECT 1", duration_ms=250.0, threshold_ms=100)
        adapter.on_error_class(
            error_class="IntegrityError",
            operation="query",
            error="synthetic",
        )
        adapter.on_migration_end(
            token=2,
            module_id="memory",
            operation="migrate",
            duration_ms=30.0,
            success=False,
            error="boom",
        )

    warnings = [
        record
        for record in caplog.records
        if "storage telemetry emit failed" in record.getMessage()
    ]
    assert len(warnings) == 5
    assert all(record.levelname == "WARNING" for record in warnings)


def test_adapter_bridges_error_class_counter() -> None:
    service = _RecordingTelemetryService()
    adapter = TelemetryServiceStorageHook(service)

    adapter.on_error_class(
        error_class="IntegrityError",
        operation="insert",
        error="duplicate key",
    )

    assert len(service.events) == 1
    event = service.events[0]
    assert event.event_type == ERROR_CLASS_EVENT_TYPE
    assert event.data["counter_name"] == "storage_error_class"
    assert event.data["counter_value"] == 1.0
    assert event.data["error_class"] == "IntegrityError"
    assert event.data["operation"] == "insert"


def test_record_store_instrumentation_emits_error_class(tmp_path: Path) -> None:
    class _RecordingHook(NoopStorageTelemetryHook):
        def __init__(self) -> None:
            self.error_classes: list[dict[str, str]] = []

        def on_error_class(
            self,
            *,
            error_class: str,
            operation: str,
            error: str,
        ) -> None:
            self.error_classes.append(
                {
                    "error_class": error_class,
                    "operation": operation,
                    "error": error,
                }
            )

    hook = _RecordingHook()
    store = RecordStoreSQLite(str(tmp_path / "storage.db"), telemetry_hook=hook)
    try:
        with pytest.raises(Exception):
            store.query_dicts("SELECT * FROM table_that_does_not_exist")
    finally:
        store.close()

    assert hook.error_classes
    assert hook.error_classes[0]["operation"] == "query"
    assert hook.error_classes[0]["error_class"]


def test_cycle_free_invariant() -> None:

    storage_root = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "openminion"
        / "modules"
        / "storage"
    )
    assert storage_root.is_dir(), storage_root

    pattern = re.compile(
        r"(?:from\s+openminion\.modules\.telemetry"
        r"|import\s+openminion\.modules\.telemetry)"
    )
    offenders: list[tuple[str, int, str]] = []
    for py_path in storage_root.rglob("*.py"):
        try:
            text = py_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                offenders.append((str(py_path), lineno, line.rstrip()))
    assert offenders == [], (
        "Storage MUST NOT import telemetry — cycle-free invariant violated:\n"
        + "\n".join(f"  {p}:{ln}: {src}" for p, ln, src in offenders)
    )


class _QueryRecordingHook:
    def __init__(self) -> None:
        self.starts: list[tuple[str, Any]] = []
        self.ends: list[tuple[Any, float, str | None]] = []
        self.slow: list[tuple[str, float, int]] = []
        self.pool: list[Mapping[str, Any]] = []
        self._token_counter = 0

    def on_pool_stats(self, stats: Mapping[str, Any]) -> None:
        self.pool.append(dict(stats))

    def on_query_start(self, sql: str, params: Any) -> Any:
        self._token_counter += 1
        self.starts.append((sql, params))
        return self._token_counter

    def on_query_end(self, token: Any, duration_ms: float, error: str | None) -> None:
        self.ends.append((token, duration_ms, error))

    def on_slow_query(self, sql: str, duration_ms: float, threshold_ms: int) -> None:
        self.slow.append((sql, duration_ms, threshold_ms))


def _build_query_store(
    tmp_path: Path,
    filename: str,
    *,
    slow_query_threshold_ms: int = 500,
) -> tuple[_QueryRecordingHook, RecordStoreSQLite]:
    hook = _QueryRecordingHook()
    store = RecordStoreSQLite(
        tmp_path / filename,
        telemetry_hook=hook,
        slow_query_threshold_ms=slow_query_threshold_ms,
    )
    return hook, store


def test_query_methods_call_hook_start_and_end(tmp_path: Path) -> None:
    hook, store = _build_query_store(tmp_path, "test.db")
    store.execute_count("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT)")
    pre_starts = len(hook.starts)
    pre_ends = len(hook.ends)

    store.insert("widgets", {"id": 1, "name": "alpha"})
    store.query_dicts("SELECT * FROM widgets")
    store.execute_count("UPDATE widgets SET name='beta' WHERE id=1")
    store.update_rows("widgets", {"id": 1}, {"name": "gamma"})
    store.query_rows("widgets")
    store.delete_rows("widgets", {"id": 1})

    new_starts = len(hook.starts) - pre_starts
    new_ends = len(hook.ends) - pre_ends
    assert new_starts == new_ends, "every start has a matching end"
    assert new_starts >= 6, f"expected >=6 query emissions, got {new_starts}"
    for _token, duration_ms, error in hook.ends[pre_ends:]:
        assert error is None
        assert duration_ms >= 0.0
    store.close()


def test_query_end_captures_error_on_failure(tmp_path: Path) -> None:
    hook, store = _build_query_store(tmp_path, "err.db")
    pre_ends = len(hook.ends)
    try:
        store.query_dicts("SELECT * FROM nonexistent_table")
    except Exception:
        pass
    new_ends = hook.ends[pre_ends:]
    assert len(new_ends) == 1
    _token, _duration, error = new_ends[0]
    assert error is not None
    assert "OperationalError" in error or "no such table" in error.lower()
    store.close()


def test_transaction_emits_begin_boundary(tmp_path: Path) -> None:
    hook, store = _build_query_store(tmp_path, "tx.db")
    store.execute_count("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    pre_starts = len(hook.starts)
    pre_ends = len(hook.ends)
    with store.transaction():
        store.execute_count("INSERT INTO t (id) VALUES (1)")
    new_starts = hook.starts[pre_starts:]
    new_ends = hook.ends[pre_ends:]
    begin_starts = [s for s in new_starts if s[0] == "BEGIN"]
    assert len(begin_starts) == 1, "transaction() emits one BEGIN start event"
    assert len(new_ends) >= len(new_starts)
    store.close()


def test_slow_query_fires_when_threshold_exceeded(tmp_path: Path) -> None:
    hook, store = _build_query_store(tmp_path, "slow.db", slow_query_threshold_ms=0)
    pre_slow = len(hook.slow)
    store.execute_count("CREATE TABLE s (id INTEGER PRIMARY KEY)")
    assert len(hook.slow) > pre_slow, (
        "with threshold=0 every query should be classified slow"
    )
    sql, duration_ms, threshold_ms = hook.slow[-1]
    assert threshold_ms == 0
    assert duration_ms > 0
    assert "CREATE TABLE" in sql
    store.close()


def test_slow_query_does_not_fire_when_under_threshold(tmp_path: Path) -> None:
    hook, store = _build_query_store(
        tmp_path,
        "fast.db",
        slow_query_threshold_ms=10_000_000,
    )
    pre_slow = len(hook.slow)
    store.execute_count("CREATE TABLE f (id INTEGER PRIMARY KEY)")
    store.insert("f", {"id": 1})
    store.query_dicts("SELECT * FROM f")
    assert len(hook.slow) == pre_slow, "fast queries must not trigger on_slow_query"
    store.close()


def test_sql_redaction_truncates_at_prefix() -> None:
    short = "SELECT 1"
    assert redact_sql(short) == short
    long = "SELECT " + "x," * 500
    redacted = redact_sql(long)
    assert len(redacted) <= SQL_LOG_PREFIX_CHARS + 3  # +"..."
    assert redacted.endswith("...")


def test_adapter_bridges_query_events(tmp_path: Path) -> None:
    recorded: list[Any] = []

    class _Svc:
        def record_event_sync(self, event: Any) -> None:
            recorded.append(event)

    adapter = TelemetryServiceStorageHook(_Svc())
    token = adapter.on_query_start("SELECT 1", None)
    assert token is not None
    adapter.on_query_end(token, 12.5, None)
    adapter.on_slow_query("SELECT 1", 250.0, 100)

    assert len(recorded) == 2
    end_event, slow_event = recorded
    assert end_event.event_type == QUERY_EVENT_TYPE
    assert end_event.data["duration_ms"] == 12.5
    assert end_event.data["error"] is None
    assert slow_event.event_type == SLOW_QUERY_EVENT_TYPE
    assert slow_event.data["duration_ms"] == 250.0
    assert slow_event.data["threshold_ms"] == 100
