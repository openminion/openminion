from __future__ import annotations

import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, cast
import warnings

from openminion.modules.storage.interfaces import STORAGE_INTERFACE_VERSION
from openminion.modules.storage.telemetry import (
    NoopStorageTelemetryHook,
    StorageTelemetryHook,
)


import re

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_ident(name: str) -> str:
    """Quote a SQL identifier, rejecting obviously dangerous values."""
    if not _IDENT_RE.match(name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return f'"{name}"'


def _resolve_sqlite_target(
    sqlite_path: str | Path,
) -> tuple[str, Path | None]:
    raw = str(sqlite_path).strip()
    if raw == ":memory:":
        return ":memory:", None
    resolved = Path(sqlite_path).expanduser().resolve(strict=False)
    return str(resolved), resolved


def configure_connection(
    connection: sqlite3.Connection,
    *,
    wal: bool = True,
    synchronous: str = "NORMAL",
    busy_timeout_ms: int = 5000,
    autocheckpoint_pages: int = 1000,
) -> None:
    """Apply storagectl default pragmas to an existing SQLite connection."""

    if wal:
        connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(f"PRAGMA synchronous={synchronous}")
    connection.execute(f"PRAGMA busy_timeout={max(0, int(busy_timeout_ms))}")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(f"PRAGMA wal_autocheckpoint={max(1, int(autocheckpoint_pages))}")


class RecordStore(ABC):
    contract_version = STORAGE_INTERFACE_VERSION

    # instance attribute populated by concrete __init__'s. Default
    # Noop preserves zero-impact behavior when no adapter is wired.
    telemetry_hook: StorageTelemetryHook
    # per-query slow-query threshold. Concrete __init__'s populate
    slow_query_threshold_ms: int = 500

    @contextmanager
    def _instrument_query(self, sql: str, params: Any) -> Iterator[None]:
        """Emit start/end (and slow-query) hook calls around a query."""

        from .telemetry import redact_sql  # local import — module-level cycle-safe

        hook = self.telemetry_hook
        redacted = redact_sql(sql)
        token = hook.on_query_start(redacted, params)
        start = time.perf_counter()
        error: str | None = None
        try:
            yield
        except BaseException as exc:  # propagate after end emit
            error = f"{type(exc).__name__}: {exc}"
            try:
                hook.on_error_class(
                    error_class=type(exc).__name__,
                    operation="query",
                    error=str(exc),
                )
            except Exception:
                pass
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            try:
                hook.on_query_end(token, duration_ms, error)
                if duration_ms > self.slow_query_threshold_ms:
                    hook.on_slow_query(
                        redacted, duration_ms, self.slow_query_threshold_ms
                    )
            except Exception:
                # Hook adapter exceptions must not interfere with storage.
                pass

    @abstractmethod
    def begin(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def commit(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def rollback(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def execute(self, sql: str, params: Iterable[Any] | None = None) -> sqlite3.Cursor:
        """Deprecated: prefer execute_count(), insert(), or update/delete helpers."""
        raise NotImplementedError

    @abstractmethod
    def executemany(self, sql: str, params: Iterable[Iterable[Any]]) -> sqlite3.Cursor:
        """Deprecated: prefer explicit write helpers or batched backend-neutral APIs."""
        raise NotImplementedError

    @abstractmethod
    def query(self, sql: str, params: Iterable[Any] | None = None) -> list[sqlite3.Row]:
        """Deprecated: prefer query_dicts() or query_rows()."""
        raise NotImplementedError

    @abstractmethod
    def healthcheck(self) -> dict[str, Any]:
        raise NotImplementedError

    def pool_health(self) -> dict[str, Any] | None:
        """Return pool statistics dict, or None if the backend has no pool."""

        return None

    @abstractmethod
    def migrate(self, schema_version: int) -> None:
        raise NotImplementedError

    def checkpoint(self, mode: str = "PASSIVE") -> tuple[int, int, int]:
        return (0, 0, 0)

    @property
    @abstractmethod
    def in_transaction(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def last_error(self) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def diagnostics(self) -> dict[str, Any]:
        raise NotImplementedError

    def capabilities(self) -> dict[str, bool]:
        return {"checkpoint": False, "raw_sql": False, "wal": False}

    @abstractmethod
    def query_dicts(
        self, sql: str, params: Iterable[Any] | None = None
    ) -> list[dict[str, Any]]:
        """Execute a read query and return results as a list of plain dicts.

        This is the engine-agnostic alternative to ``query()`` which returns
        ``sqlite3.Row`` objects.  New code should prefer this method.
        """
        raise NotImplementedError

    @abstractmethod
    def execute_count(self, sql: str, params: Iterable[Any] | None = None) -> int:
        """Execute a write statement and return the number of rows affected.

        This is the engine-agnostic alternative to inspecting
        ``sqlite3.Cursor.rowcount`` after ``execute()``.
        """
        raise NotImplementedError

    @abstractmethod
    def insert(self, table: str, row: dict[str, Any]) -> int:
        """Insert a single row and return the last-inserted row ID.

        ``row`` maps column names to values.  Column names are quoted as
        identifiers; values are passed as parameters.
        """
        raise NotImplementedError

    @abstractmethod
    def query_rows(
        self,
        table: str,
        where: dict[str, Any] | None = None,
        order: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Query rows from *table* with optional WHERE/ORDER/LIMIT.

        ``where`` maps column names to equality values (AND-joined).
        Returns plain dicts (engine-agnostic).
        """
        raise NotImplementedError

    @abstractmethod
    def update_rows(
        self, table: str, where: dict[str, Any], values: dict[str, Any]
    ) -> int:
        """Update rows matching *where* with *values*.  Returns affected count."""
        raise NotImplementedError

    @abstractmethod
    def delete_rows(self, table: str, where: dict[str, Any]) -> int:
        """Delete rows matching *where*.  Returns affected count."""
        raise NotImplementedError

    def insert_many(self, table: str, rows: list[dict[str, Any]]) -> int:
        """Insert multiple rows in a single batched operation."""
        if not rows:
            return 0
        first_columns = set(rows[0].keys())
        if any(set(row.keys()) != first_columns for row in rows[1:]):
            raise ValueError("insert_many requires all rows to share the same columns")
        for row in rows:
            self.insert(table, row)
        return len(rows)

    def stream_dicts(
        self,
        sql: str,
        params: Iterable[Any] | None = None,
        batch_size: int = 500,
    ) -> Iterator[dict[str, Any]]:
        """Stream rows for *sql* one dict at a time."""
        rows = self.query_dicts(sql, params)
        for row in rows:
            yield row

    def stream_rows(
        self,
        table: str,
        where: dict[str, Any] | None = None,
        order: str | None = None,
        batch_size: int = 500,
    ) -> Iterator[dict[str, Any]]:
        """Stream rows for ``table`` matching ``where`` one dict at a time."""
        rows = self.query_rows(table, where=where, order=order)
        for row in rows:
            yield row

    @contextmanager
    def transaction(self) -> Iterator[None]:
        # emit BEGIN/COMMIT or BEGIN/ROLLBACK telemetry boundary around
        with self._instrument_query("BEGIN", None):
            self.begin()
            try:
                yield
            except Exception:
                self.rollback()
                raise
            else:
                self.commit()


class RecordStoreSQLite(RecordStore):
    contract_version = STORAGE_INTERFACE_VERSION

    def __init__(
        self,
        sqlite_path: str | Path,
        *,
        wal: bool = True,
        synchronous: str = "NORMAL",
        busy_timeout_ms: int = 5000,
        autocheckpoint_pages: int = 1000,
        telemetry_hook: StorageTelemetryHook | None = None,
        slow_query_threshold_ms: int = 500,
    ) -> None:
        sqlite_target, resolved_path = _resolve_sqlite_target(sqlite_path)
        self.sqlite_path = resolved_path or Path(":memory:")
        self._is_memory = resolved_path is None
        if resolved_path is not None:
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._in_tx = False
        self._last_error: str | None = None
        # SQLite has no connection pool; the hook is stored for
        self.telemetry_hook = telemetry_hook or NoopStorageTelemetryHook()
        # slow-query threshold (ms) consulted by `_instrument_query`
        self.slow_query_threshold_ms = int(slow_query_threshold_ms)

        self._conn = sqlite3.connect(sqlite_target, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        configure_connection(
            self._conn,
            wal=wal,
            synchronous=synchronous,
            busy_timeout_ms=busy_timeout_ms,
            autocheckpoint_pages=autocheckpoint_pages,
        )

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def begin(self) -> None:
        with self._lock:
            if self._in_tx:
                return
            try:
                self._conn.execute("BEGIN IMMEDIATE")
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise
            else:
                self._last_error = None
                self._in_tx = True

    def commit(self) -> None:
        with self._lock:
            if not self._in_tx:
                return
            try:
                self._conn.commit()
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise
            else:
                self._last_error = None
                self._in_tx = False

    def rollback(self) -> None:
        with self._lock:
            if not self._in_tx:
                return
            try:
                self._conn.rollback()
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise
            else:
                self._last_error = None
                self._in_tx = False

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> sqlite3.Cursor:
        warnings.warn(
            "RecordStoreSQLite.execute() is deprecated; use insert(), execute_count(), "
            "query_rows(), or query_dicts() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        with self._lock:
            try:
                cursor = self._conn.execute(sql, tuple(params or ()))
                if not self._in_tx:
                    self._conn.commit()
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise
            else:
                self._last_error = None
                return cursor

    def executemany(self, sql: str, params: Iterable[Iterable[Any]]) -> sqlite3.Cursor:
        warnings.warn(
            "RecordStoreSQLite.executemany() is deprecated; prefer explicit "
            "backend-neutral write helpers.",
            DeprecationWarning,
            stacklevel=2,
        )
        with self._lock:
            try:
                cursor = self._conn.executemany(sql, cast(Iterable[Any], params))
                if not self._in_tx:
                    self._conn.commit()
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise
            else:
                self._last_error = None
                return cursor

    def query(self, sql: str, params: Iterable[Any] | None = None) -> list[sqlite3.Row]:
        warnings.warn(
            "RecordStoreSQLite.query() is deprecated; use query_dicts() or "
            "query_rows() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        with self._lock:
            try:
                rows = self._conn.execute(sql, tuple(params or ())).fetchall()
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise
            else:
                self._last_error = None
                return rows

    def query_dicts(
        self, sql: str, params: Iterable[Any] | None = None
    ) -> list[dict[str, Any]]:
        with self._instrument_query(sql, params), self._lock:
            try:
                rows = self._conn.execute(sql, tuple(params or ())).fetchall()
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise
            else:
                self._last_error = None
                return [dict(row) for row in rows]

    def execute_count(self, sql: str, params: Iterable[Any] | None = None) -> int:
        with self._instrument_query(sql, params), self._lock:
            try:
                cursor = self._conn.execute(sql, tuple(params or ()))
                if not self._in_tx:
                    self._conn.commit()
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise
            else:
                self._last_error = None
                return cursor.rowcount

    def insert(self, table: str, row: dict[str, Any]) -> int:
        cols = [_quote_ident(c) for c in row]
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT INTO {_quote_ident(table)} ({', '.join(cols)}) VALUES ({placeholders})"
        with self._instrument_query(sql, row), self._lock:
            try:
                cursor = self._conn.execute(sql, tuple(row.values()))
                if not self._in_tx:
                    self._conn.commit()
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise
            else:
                self._last_error = None
                return cursor.lastrowid or 0

    def insert_many(self, table: str, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        first_columns = list(rows[0].keys())
        first_set = set(first_columns)
        if any(set(row.keys()) != first_set for row in rows[1:]):
            raise ValueError("insert_many requires all rows to share the same columns")
        cols = [_quote_ident(c) for c in first_columns]
        placeholders = ", ".join("?" for _ in cols)
        sql = (
            f"INSERT INTO {_quote_ident(table)} ({', '.join(cols)}) "
            f"VALUES ({placeholders})"
        )
        # Materialise once in deterministic column order before sharing the
        # connection-bound cursor so we don't re-iterate dict mutations.
        param_rows = [tuple(row[col] for col in first_columns) for row in rows]
        with self._lock:
            try:
                self._conn.executemany(sql, param_rows)
                if not self._in_tx:
                    self._conn.commit()
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise
            else:
                self._last_error = None
                return len(rows)

    def stream_dicts(
        self,
        sql: str,
        params: Iterable[Any] | None = None,
        batch_size: int = 500,
    ) -> Iterator[dict[str, Any]]:
        with self._lock:
            try:
                cursor = self._conn.execute(sql, tuple(params or ()))
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise
            self._last_error = None
            try:
                while True:
                    batch = cursor.fetchmany(max(1, batch_size))
                    if not batch:
                        break
                    for row in batch:
                        yield dict(row)
            finally:
                cursor.close()

    def stream_rows(
        self,
        table: str,
        where: dict[str, Any] | None = None,
        order: str | None = None,
        batch_size: int = 500,
    ) -> Iterator[dict[str, Any]]:
        parts: list[str] = [f"SELECT * FROM {_quote_ident(table)}"]
        params: list[Any] = []
        if where:
            clauses = [f"{_quote_ident(k)} = ?" for k in where]
            parts.append("WHERE " + " AND ".join(clauses))
            params.extend(where.values())
        if order:
            parts.append(f"ORDER BY {order}")
        sql = " ".join(parts)
        yield from self.stream_dicts(sql, params, batch_size=batch_size)

    def query_rows(
        self,
        table: str,
        where: dict[str, Any] | None = None,
        order: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        parts: list[str] = [f"SELECT * FROM {_quote_ident(table)}"]
        params: list[Any] = []
        if where:
            clauses = [f"{_quote_ident(k)} = ?" for k in where]
            parts.append("WHERE " + " AND ".join(clauses))
            params.extend(where.values())
        if order:
            parts.append(f"ORDER BY {order}")
        if limit is not None:
            parts.append("LIMIT ?")
            params.append(limit)
        sql = " ".join(parts)
        return self.query_dicts(sql, params)

    def update_rows(
        self, table: str, where: dict[str, Any], values: dict[str, Any]
    ) -> int:
        set_clauses = [f"{_quote_ident(k)} = ?" for k in values]
        where_clauses = [f"{_quote_ident(k)} = ?" for k in where]
        sql = (
            f"UPDATE {_quote_ident(table)} SET {', '.join(set_clauses)} "
            f"WHERE {' AND '.join(where_clauses)}"
        )
        params = list(values.values()) + list(where.values())
        return self.execute_count(sql, params)

    def delete_rows(self, table: str, where: dict[str, Any]) -> int:
        where_clauses = [f"{_quote_ident(k)} = ?" for k in where]
        sql = f"DELETE FROM {_quote_ident(table)} WHERE {' AND '.join(where_clauses)}"
        params = list(where.values())
        return self.execute_count(sql, params)

    def healthcheck(self) -> dict[str, Any]:
        with self._lock:
            try:
                row = self._conn.execute("SELECT 1 AS ok").fetchone()
                self._last_error = None
                return {"ok": bool(row and int(row["ok"]) == 1), "error": None}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}

    def migrate(self, schema_version: int) -> None:
        with self._lock:
            try:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_version (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        version INTEGER NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                self._conn.execute(
                    """
                    INSERT INTO schema_version(id, version, updated_at)
                    VALUES (1, ?, datetime('now'))
                    ON CONFLICT(id) DO UPDATE SET
                        version=excluded.version,
                        updated_at=excluded.updated_at
                    """,
                    (int(schema_version),),
                )
                self._conn.commit()
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise
            else:
                self._last_error = None

    def checkpoint(self, mode: str = "PASSIVE") -> tuple[int, int, int]:
        normalized = str(mode or "PASSIVE").strip().upper()
        if normalized not in {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}:
            normalized = "PASSIVE"
        with self._lock:
            try:
                row = self._conn.execute(
                    f"PRAGMA wal_checkpoint({normalized})"
                ).fetchone()
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise
            else:
                self._last_error = None
        if row is None:
            return (0, 0, 0)
        return (int(row[0]), int(row[1]), int(row[2]))

    @property
    def in_transaction(self) -> bool:
        return self._in_tx

    def last_error(self) -> str | None:
        return self._last_error

    def diagnostics(self) -> dict[str, Any]:
        wal_path = None if self._is_memory else Path(f"{self.sqlite_path}-wal")
        shm_path = None if self._is_memory else Path(f"{self.sqlite_path}-shm")
        with self._lock:
            journal_mode = self._conn.execute("PRAGMA journal_mode").fetchone()
            page_count = self._conn.execute("PRAGMA page_count").fetchone()
            freelist_count = self._conn.execute("PRAGMA freelist_count").fetchone()
            wal_autocheckpoint = self._conn.execute(
                "PRAGMA wal_autocheckpoint"
            ).fetchone()
            busy_timeout = self._conn.execute("PRAGMA busy_timeout").fetchone()
            synchronous = self._conn.execute("PRAGMA synchronous").fetchone()
            wal_stats = self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()

        return {
            "journal_mode": journal_mode[0] if journal_mode else None,
            "page_count": int(page_count[0]) if page_count else 0,
            "freelist_count": int(freelist_count[0]) if freelist_count else 0,
            "wal_autocheckpoint": int(wal_autocheckpoint[0])
            if wal_autocheckpoint
            else None,
            "busy_timeout_ms": int(busy_timeout[0]) if busy_timeout else None,
            "synchronous": synchronous[0] if synchronous else None,
            "wal_checkpoint": tuple(int(x) for x in wal_stats)
            if wal_stats
            else (0, 0, 0),
            "wal_file_bytes": wal_path.stat().st_size
            if wal_path and wal_path.exists()
            else 0,
            "shm_file_bytes": shm_path.stat().st_size
            if shm_path and shm_path.exists()
            else 0,
            "last_error": self._last_error,
        }

    def capabilities(self) -> dict[str, bool]:
        return {"checkpoint": True, "raw_sql": True, "wal": True}
