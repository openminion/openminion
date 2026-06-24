from __future__ import annotations

import re
import threading
import time
from typing import TYPE_CHECKING, Any, Iterable, Iterator, Mapping

from openminion.modules.storage.record_store import RecordStore
from openminion.modules.storage.telemetry import (
    NoopStorageTelemetryHook,
    StorageTelemetryHook,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ORDER_IDENT = r'(?:[A-Za-z_][A-Za-z0-9_]*|"[A-Za-z_][A-Za-z0-9_]*")'
_ORDER_RE = re.compile(
    rf"^{_ORDER_IDENT}(?:\s+(?:ASC|DESC))?"
    rf"(?:\s*,\s*{_ORDER_IDENT}(?:\s+(?:ASC|DESC))?)*$",
    re.IGNORECASE,
)


def _quote_ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return f'"{name}"'


def _normalize_sql_params(
    sql: str,
    params: Mapping[str, Any] | Iterable[Any] | None,
) -> tuple[str, dict[str, Any]]:
    if params is None:
        return sql, {}
    if isinstance(params, Mapping):
        return sql, dict(params)

    values = tuple(params)
    if not values:
        return sql, {}

    pieces = sql.split("?")
    if len(pieces) == 1:
        raise ValueError("Positional params require '?' placeholders for postgres")
    if len(pieces) - 1 != len(values):
        raise ValueError("Placeholder count does not match positional params")

    rewritten: list[str] = [pieces[0]]
    named: dict[str, Any] = {}
    for idx, piece in enumerate(pieces[1:]):
        key = f"p{idx}"
        rewritten.append(f":{key}")
        rewritten.append(piece)
        named[key] = values[idx]
    return "".join(rewritten), named


class RecordStorePostgres(RecordStore):
    contract_version = "v1"

    def __init__(
        self,
        engine_or_url: Engine | str,
        *,
        pool_recycle_seconds: int | None = None,
        pool_size: int | None = None,
        pool_max_overflow: int | None = None,
        pool_timeout_seconds: float | None = None,
        telemetry_hook: StorageTelemetryHook | None = None,
        slow_query_threshold_ms: int = 500,
    ) -> None:
        self.telemetry_hook = telemetry_hook or NoopStorageTelemetryHook()
        self.slow_query_threshold_ms = int(slow_query_threshold_ms)
        try:
            from sqlalchemy import create_engine
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "sqlalchemy is required for record.postgres; install openminion[postgres]"
            ) from exc

        self._owns_engine = isinstance(engine_or_url, str)
        if isinstance(engine_or_url, str):
            engine_kwargs: dict[str, Any] = {"future": True}
            if pool_recycle_seconds is not None:
                engine_kwargs["pool_recycle"] = int(pool_recycle_seconds)
            if pool_size is not None:
                engine_kwargs["pool_size"] = int(pool_size)
            if pool_max_overflow is not None:
                engine_kwargs["max_overflow"] = int(pool_max_overflow)
            if pool_timeout_seconds is not None:
                engine_kwargs["pool_timeout"] = float(pool_timeout_seconds)
            self._engine = create_engine(engine_or_url, **engine_kwargs)
        else:
            self._engine = engine_or_url
        self._connection: Connection | None = None
        self._transaction = None
        self._lock = threading.RLock()
        self._last_error: str | None = None
        self._connection_birthdays: dict[int, float] = {}
        self._connection_birthdays_lock = threading.Lock()
        self._install_pool_event_listeners()

    def _install_pool_event_listeners(self) -> None:
        try:
            from sqlalchemy import event as sa_event
        except Exception:  # noqa: BLE001
            return
        pool = getattr(self._engine, "pool", None)
        if pool is None:
            return

        def _on_connect(dbapi_conn: Any, conn_record: Any) -> None:  # noqa: ARG001
            with self._connection_birthdays_lock:
                self._connection_birthdays[id(conn_record)] = time.monotonic()

        def _on_close(dbapi_conn: Any, conn_record: Any) -> None:  # noqa: ARG001
            with self._connection_birthdays_lock:
                self._connection_birthdays.pop(id(conn_record), None)

        try:
            sa_event.listen(pool, "connect", _on_connect)
            sa_event.listen(pool, "close", _on_close)
            sa_event.listen(pool, "close_detached", _on_close)
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        with self._lock:
            if self._transaction is not None:
                try:
                    self._transaction.rollback()
                except Exception:  # noqa: BLE001
                    pass
                self._transaction = None
            if self._connection is not None:
                try:
                    self._connection.close()
                except Exception:  # noqa: BLE001
                    pass
                self._connection = None
            if self._owns_engine:
                self._engine.dispose()

    def begin(self) -> None:
        with self._lock:
            if self._transaction is not None:
                return
            try:
                self._connection = self._engine.connect()
                self._transaction = self._connection.begin()
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                if self._connection is not None:
                    self._connection.close()
                    self._connection = None
                self._transaction = None
                raise
            else:
                self._last_error = None

    def commit(self) -> None:
        with self._lock:
            if self._transaction is None or self._connection is None:
                return
            try:
                self._transaction.commit()
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise
            else:
                self._last_error = None
            finally:
                self._transaction = None
                self._connection.close()
                self._connection = None

    def rollback(self) -> None:
        with self._lock:
            if self._transaction is None or self._connection is None:
                return
            try:
                self._transaction.rollback()
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise
            else:
                self._last_error = None
            finally:
                self._transaction = None
                self._connection.close()
                self._connection = None

    def execute(self, sql: str, params: Iterable[Any] | None = None):
        del sql, params
        raise NotImplementedError(
            "Use backend-neutral methods (insert, query_rows, query_dicts, etc.) "
            "instead of raw SQL. This backend does not support sqlite3-typed returns."
        )

    def executemany(self, sql: str, params: Iterable[Iterable[Any]]):
        del sql, params
        raise NotImplementedError(
            "Use backend-neutral methods (insert, query_rows, query_dicts, etc.) "
            "instead of raw SQL. This backend does not support sqlite3-typed returns."
        )

    def query(self, sql: str, params: Iterable[Any] | None = None):
        del sql, params
        raise NotImplementedError(
            "Use backend-neutral methods (insert, query_rows, query_dicts, etc.) "
            "instead of raw SQL. This backend does not support sqlite3-typed returns."
        )

    def _execute_mappings(
        self,
        sql: str,
        params: Mapping[str, Any] | Iterable[Any] | None = None,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text

        statement_sql, bind_params = _normalize_sql_params(sql, params)
        if self._connection is not None:
            result = self._connection.execute(text(statement_sql), bind_params)
            return [dict(row) for row in result.mappings().all()]
        with self._engine.connect() as connection:
            result = connection.execute(text(statement_sql), bind_params)
            return [dict(row) for row in result.mappings().all()]

    def _execute_rowcount(
        self,
        sql: str,
        params: Mapping[str, Any] | Iterable[Any] | None = None,
    ) -> int:
        from sqlalchemy import text

        statement_sql, bind_params = _normalize_sql_params(sql, params)
        if self._connection is not None:
            result = self._connection.execute(text(statement_sql), bind_params)
            return int(result.rowcount or 0)
        with self._engine.begin() as connection:
            result = connection.execute(text(statement_sql), bind_params)
            return int(result.rowcount or 0)

    def query_dicts(
        self,
        sql: str,
        params: Mapping[str, Any] | Iterable[Any] | None = None,
    ) -> list[dict[str, Any]]:
        with self._instrument_query(sql, params), self._lock:
            try:
                rows = self._execute_mappings(sql, params)
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise
            else:
                self._last_error = None
                return rows

    def execute_count(
        self,
        sql: str,
        params: Mapping[str, Any] | Iterable[Any] | None = None,
    ) -> int:
        with self._instrument_query(sql, params), self._lock:
            try:
                count = self._execute_rowcount(sql, params)
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise
            else:
                self._last_error = None
                return count

    def insert(self, table: str, row: dict[str, Any]) -> int:
        columns = list(row.keys())
        if not columns:
            raise ValueError("row must contain at least one column")
        quoted_columns = [_quote_ident(column) for column in columns]
        bind_names = [f"v{idx}" for idx, _ in enumerate(columns)]
        sql = (
            f"INSERT INTO {_quote_ident(table)} ({', '.join(quoted_columns)}) "
            f"VALUES ({', '.join(f':{name}' for name in bind_names)})"
        )
        params = {name: row[column] for name, column in zip(bind_names, columns)}
        self.execute_count(sql, params)
        explicit_id = row.get("id")
        return int(explicit_id) if explicit_id is not None else 0

    def insert_many(self, table: str, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        first_columns = list(rows[0].keys())
        first_set = set(first_columns)
        if any(set(row.keys()) != first_set for row in rows[1:]):
            raise ValueError("insert_many requires all rows to share the same columns")
        if not first_columns:
            raise ValueError("rows must contain at least one column")

        from sqlalchemy import text

        quoted_columns = [_quote_ident(c) for c in first_columns]
        bind_names = [f"v{idx}" for idx, _ in enumerate(first_columns)]
        sql = (
            f"INSERT INTO {_quote_ident(table)} ({', '.join(quoted_columns)}) "
            f"VALUES ({', '.join(f':{name}' for name in bind_names)})"
        )
        param_rows = [
            {name: row[column] for name, column in zip(bind_names, first_columns)}
            for row in rows
        ]
        with self._lock:
            try:
                if self._connection is not None:
                    self._connection.execute(text(sql), param_rows)
                else:
                    with self._engine.begin() as connection:
                        connection.execute(text(sql), param_rows)
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise
            else:
                self._last_error = None
                return len(rows)

    def stream_dicts(
        self,
        sql: str,
        params: Mapping[str, Any] | Iterable[Any] | None = None,
        batch_size: int = 500,
    ) -> Iterator[dict[str, Any]]:
        from sqlalchemy import text

        statement_sql, bind_params = _normalize_sql_params(sql, params)
        statement = text(statement_sql)

        def _yield_from_connection(
            connection: "Connection",
        ) -> Iterator[dict[str, Any]]:
            result = connection.execute(statement, bind_params)
            try:
                while True:
                    batch = result.mappings().fetchmany(max(1, batch_size))
                    if not batch:
                        break
                    for row in batch:
                        yield dict(row)
            finally:
                result.close()

        with self._lock:
            self._last_error = None
            if self._connection is not None:
                # Caller is inside an explicit transaction: stream from the
                # bound connection so cursor lifetime matches the transaction.
                yield from _yield_from_connection(self._connection)
                return
        # No active transaction: open a short-lived connection for streaming.
        with self._engine.connect() as connection:
            yield from _yield_from_connection(connection)

    def stream_rows(
        self,
        table: str,
        where: dict[str, Any] | None = None,
        order: str | None = None,
        batch_size: int = 500,
    ) -> Iterator[dict[str, Any]]:
        parts: list[str] = [f"SELECT * FROM {_quote_ident(table)}"]
        bind_params: dict[str, Any] = {}
        if where:
            clauses: list[str] = []
            for idx, (key, value) in enumerate(where.items()):
                bind_name = f"w{idx}"
                clauses.append(f"{_quote_ident(key)} = :{bind_name}")
                bind_params[bind_name] = value
            parts.append("WHERE " + " AND ".join(clauses))
        if order:
            if not _ORDER_RE.match(order):
                raise ValueError(f"Invalid ORDER BY expression: {order!r}")
            parts.append(f"ORDER BY {order}")
        yield from self.stream_dicts(
            " ".join(parts), bind_params, batch_size=batch_size
        )

    def query_rows(
        self,
        table: str,
        where: dict[str, Any] | None = None,
        order: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        parts: list[str] = [f"SELECT * FROM {_quote_ident(table)}"]
        params: dict[str, Any] = {}
        if where:
            clauses: list[str] = []
            for idx, (key, value) in enumerate(where.items()):
                bind_name = f"w{idx}"
                clauses.append(f"{_quote_ident(key)} = :{bind_name}")
                params[bind_name] = value
            parts.append("WHERE " + " AND ".join(clauses))
        if order:
            if not _ORDER_RE.match(order):
                raise ValueError(f"Invalid ORDER BY expression: {order!r}")
            parts.append(f"ORDER BY {order}")
        if limit is not None:
            parts.append("LIMIT :limit")
            params["limit"] = int(limit)
        return self.query_dicts(" ".join(parts), params)

    def update_rows(
        self, table: str, where: dict[str, Any], values: dict[str, Any]
    ) -> int:
        params: dict[str, Any] = {}
        set_clauses: list[str] = []
        for idx, (key, value) in enumerate(values.items()):
            bind_name = f"v{idx}"
            set_clauses.append(f"{_quote_ident(key)} = :{bind_name}")
            params[bind_name] = value
        where_clauses: list[str] = []
        for idx, (key, value) in enumerate(where.items()):
            bind_name = f"w{idx}"
            where_clauses.append(f"{_quote_ident(key)} = :{bind_name}")
            params[bind_name] = value
        sql = (
            f"UPDATE {_quote_ident(table)} SET {', '.join(set_clauses)} "
            f"WHERE {' AND '.join(where_clauses)}"
        )
        return self.execute_count(sql, params)

    def delete_rows(self, table: str, where: dict[str, Any]) -> int:
        params: dict[str, Any] = {}
        where_clauses: list[str] = []
        for idx, (key, value) in enumerate(where.items()):
            bind_name = f"w{idx}"
            where_clauses.append(f"{_quote_ident(key)} = :{bind_name}")
            params[bind_name] = value
        sql = f"DELETE FROM {_quote_ident(table)} WHERE {' AND '.join(where_clauses)}"
        return self.execute_count(sql, params)

    def healthcheck(self) -> dict[str, Any]:
        try:
            rows = self.query_dicts("SELECT 1 AS ok")
            result: dict[str, Any] = {
                "ok": bool(rows and int(rows[0]["ok"]) == 1),
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            result = {"ok": False, "error": str(exc)}
        pool_stats = self.pool_health()
        if pool_stats is not None:
            result["pool"] = pool_stats
        return result

    def pool_health(self) -> dict[str, Any] | None:
        pool = getattr(self._engine, "pool", None)
        if pool is None:
            return None
        stats: dict[str, Any] = {}

        def _safe_int(method_name: str) -> int | None:
            method = getattr(pool, method_name, None)
            if not callable(method):
                return None
            try:
                return int(method())
            except Exception:  # noqa: BLE001
                return None

        stats["pool_size"] = _safe_int("size")
        stats["checked_out"] = _safe_int("checkedout")
        stats["overflow"] = _safe_int("overflow")

        oldest_age: float | None = None
        with self._connection_birthdays_lock:
            if self._connection_birthdays:
                oldest = min(self._connection_birthdays.values())
                oldest_age = max(0.0, time.monotonic() - oldest)
        stats["oldest_connection_age_seconds"] = (
            None if oldest_age is None else round(oldest_age, 3)
        )
        stats["backend"] = "postgres"
        return stats

    def migrate(self, schema_version: int) -> None:
        self.execute_count(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                id INTEGER PRIMARY KEY,
                version INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.execute_count(
            """
            INSERT INTO schema_version(id, version, updated_at)
            VALUES (1, :version, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                version = EXCLUDED.version,
                updated_at = EXCLUDED.updated_at
            """,
            {"version": int(schema_version)},
        )

    @property
    def in_transaction(self) -> bool:
        return self._transaction is not None

    def last_error(self) -> str | None:
        return self._last_error

    def diagnostics(self) -> dict[str, Any]:
        return {
            "backend": "postgres",
            "dialect": self._engine.dialect.name,
            "in_transaction": self.in_transaction,
            "url": self._engine.url.render_as_string(hide_password=True),
            "last_error": self._last_error,
        }

    def capabilities(self) -> dict[str, bool]:
        return {"checkpoint": False, "raw_sql": False, "wal": False}


__all__ = ["RecordStorePostgres"]
