from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from openminion.modules.storage.record_store import RecordStore

from openminion.base.time import utc_now_iso as _utc_now_iso


def _json_payload(payload: Mapping[str, Any] | None) -> str:
    return json.dumps(dict(payload or {}), sort_keys=True)


def _parse_json(raw: str) -> dict[str, Any]:
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        return parsed
    return {}


@dataclass(frozen=True)
class IdempotencyRecord:
    method: str
    idempotency_key: str
    request_hash: str
    response: dict[str, Any]
    status: str
    created_at: str
    updated_at: str


class IdempotencyStore:
    def __init__(self, store_or_connection: RecordStore | sqlite3.Connection) -> None:
        if isinstance(store_or_connection, sqlite3.Connection):
            self._record_store: RecordStore | None = None
            self._conn: sqlite3.Connection | None = store_or_connection
        else:
            self._record_store = store_or_connection
            self._conn = None

    def _query_one(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] | None = None,
    ) -> dict[str, Any] | None:
        if self._record_store is not None:
            rows = self._record_store.query_dicts(sql, params)
            return rows[0] if rows else None
        row = self._conn.execute(sql, params or ()).fetchone()  # type: ignore[union-attr]
        return dict(row) if row is not None else None

    def _execute_count(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] | None = None,
    ) -> int:
        if self._record_store is not None:
            return self._record_store.execute_count(sql, params)
        cursor = self._conn.execute(sql, params or ())  # type: ignore[union-attr]
        self._conn.commit()  # type: ignore[union-attr]
        return int(cursor.rowcount or 0)

    def reserve(
        self, *, method: str, idempotency_key: str, request_hash: str = ""
    ) -> bool:
        now = _utc_now_iso()
        inserted = self._execute_count(
            """
            INSERT INTO idempotency_keys(
                method,
                idempotency_key,
                request_hash,
                response_json,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, 'in_progress', ?, ?)
            ON CONFLICT(method, idempotency_key) DO NOTHING
            """,
            (method, idempotency_key, request_hash, "{}", now, now),
        )
        return inserted > 0

    def get(self, *, method: str, idempotency_key: str) -> Optional[IdempotencyRecord]:
        row = self._query_one(
            """
            SELECT method, idempotency_key, request_hash, response_json, status, created_at, updated_at
            FROM idempotency_keys
            WHERE method = ? AND idempotency_key = ?
            """,
            (method, idempotency_key),
        )
        if row is None:
            return None
        return _row_to_record(row)

    def complete(
        self,
        *,
        method: str,
        idempotency_key: str,
        response: Mapping[str, Any] | None = None,
        status: str = "completed",
        request_hash: str = "",
    ) -> IdempotencyRecord:
        existing = self.get(method=method, idempotency_key=idempotency_key)
        now = _utc_now_iso()
        if existing is None:
            self._execute_count(
                """
                INSERT INTO idempotency_keys(
                    method,
                    idempotency_key,
                    request_hash,
                    response_json,
                    status,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    method,
                    idempotency_key,
                    request_hash,
                    _json_payload(response),
                    status,
                    now,
                    now,
                ),
            )
        else:
            next_request_hash = request_hash or existing.request_hash
            self._execute_count(
                """
                UPDATE idempotency_keys
                SET request_hash = ?, response_json = ?, status = ?, updated_at = ?
                WHERE method = ? AND idempotency_key = ?
                """,
                (
                    next_request_hash,
                    _json_payload(response),
                    status,
                    now,
                    method,
                    idempotency_key,
                ),
            )

        refreshed = self.get(method=method, idempotency_key=idempotency_key)
        if refreshed is None:
            raise RuntimeError(
                f"Failed to load idempotency record method={method} idempotency_key={idempotency_key}"
            )
        return refreshed


def _row_to_record(row: Mapping[str, Any]) -> IdempotencyRecord:
    return IdempotencyRecord(
        method=str(row["method"]),
        idempotency_key=str(row["idempotency_key"]),
        request_hash=str(row["request_hash"]),
        response=_parse_json(str(row["response_json"])),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
