from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from openminion.modules.storage.record_store import RecordStore

from .models import MessageRecord
from .rows import MESSAGE_COLUMNS, row_to_message


class RuntimeSessionStoreBackend:
    def __init__(self, store_or_connection: RecordStore | sqlite3.Connection) -> None:
        if isinstance(store_or_connection, sqlite3.Connection):
            self._record_store: RecordStore | None = None
            self._conn: sqlite3.Connection | None = store_or_connection
        else:
            self._record_store = store_or_connection
            self._conn = None
        self._raw_tx_depth = 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._record_store is not None:
            with self._record_store.transaction():
                yield
            return

        if self._conn is None:
            raise RuntimeError("session store has no backing connection")
        self._raw_tx_depth += 1
        if self._raw_tx_depth == 1:
            self._conn.execute("BEGIN")
        try:
            yield
        except Exception:
            if self._raw_tx_depth == 1:
                self._conn.rollback()
            raise
        else:
            if self._raw_tx_depth == 1:
                self._conn.commit()
        finally:
            self._raw_tx_depth -= 1

    def query_dicts(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        if self._record_store is not None:
            return self._record_store.query_dicts(sql, params)
        if self._conn is None:
            raise RuntimeError("session store has no backing connection")
        rows = self._conn.execute(sql, params or ()).fetchall()
        return [dict(row) for row in rows]

    def query_one(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] | None = None,
    ) -> dict[str, Any] | None:
        rows = self.query_dicts(sql, params)
        return rows[0] if rows else None

    def execute_count(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] | None = None,
    ) -> int:
        if self._record_store is not None:
            return self._record_store.execute_count(sql, params)
        if self._conn is None:
            raise RuntimeError("session store has no backing connection")
        cursor = self._conn.execute(sql, params or ())
        if self._raw_tx_depth == 0:
            self._conn.commit()
        return int(cursor.rowcount or 0)

    def message_query(
        self,
        *,
        where_clause: str,
        params: list[object],
        newest_first: bool,
        limit: int,
        after_rowid: int | None = None,
    ) -> list[dict[str, Any]]:
        outer_where = [where_clause.replace("WHERE ", "", 1)]
        outer_params = list(params)
        if after_rowid is not None:
            outer_where.append("rowid > ?")
            outer_params.append(max(0, int(after_rowid)))
        outer_sql = "WHERE " + " AND ".join(outer_where)
        direction = "DESC" if newest_first else "ASC"
        query = f"""
            SELECT rowid, {MESSAGE_COLUMNS}
            FROM (
                SELECT
                    ROW_NUMBER() OVER (
                        PARTITION BY session_id
                        ORDER BY created_at ASC, id ASC
                    ) AS rowid,
                    {MESSAGE_COLUMNS}
                FROM messages
            ) AS numbered
            {outer_sql}
            ORDER BY rowid {direction}
            LIMIT ?
        """
        outer_params.append(max(0, int(limit)))
        return self.query_dicts(query, outer_params)

    def message_by_id(self, message_id: str) -> MessageRecord:
        row = self.query_one(
            f"""
            SELECT rowid, {MESSAGE_COLUMNS}
            FROM (
                SELECT
                    ROW_NUMBER() OVER (
                        PARTITION BY session_id
                        ORDER BY created_at ASC, id ASC
                    ) AS rowid,
                    {MESSAGE_COLUMNS}
                FROM messages
            ) AS numbered
            WHERE id = ?
            """,
            (message_id,),
        )
        if row is None:
            raise RuntimeError(f"Failed to load message after insert: {message_id}")
        return row_to_message(row)
