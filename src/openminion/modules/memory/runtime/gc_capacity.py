from collections.abc import Mapping, Sequence
from sqlite3 import Row as SQLiteRow
from typing import Any

from sqlalchemy import text

from openminion.base.time import utc_now
from openminion.modules.memory.runtime.gc_records import (
    remove_collected_artifact_refs,
    soft_delete_postgres_record,
    soft_delete_sqlite_record,
)
from openminion.modules.memory.storage.base import MemoryStore
from openminion.modules.memory.storage.postgres.store import PostgresMemoryStore


def enforce_scope_capacity(
    store: MemoryStore,
    *,
    max_records: int,
    exempt_types: list[str] | tuple[str, ...] = ("pin",),
) -> dict[str, int]:
    exempt = {str(item) for item in exempt_types}
    now_iso = utc_now().isoformat()

    if isinstance(store, PostgresMemoryStore):
        evicted, removed_edges = _enforce_postgres_scope_capacity(
            store,
            max_records=max_records,
            exempt=exempt,
            now_iso=now_iso,
        )
        remove_collected_artifact_refs(store, removed_edges)
        return evicted

    evicted, removed_edges = _enforce_sqlite_scope_capacity(
        store,
        max_records=max_records,
        exempt=exempt,
        now_iso=now_iso,
    )
    remove_collected_artifact_refs(store, removed_edges)
    return evicted


def _enforce_postgres_scope_capacity(
    store: PostgresMemoryStore,
    *,
    max_records: int,
    exempt: set[str],
    now_iso: str,
) -> tuple[dict[str, int], list[tuple[str, list[Any]]]]:
    evicted: dict[str, int] = {}
    removed_edges: list[tuple[str, list[Any]]] = []
    with store.gc_connection() as conn:
        scopes = [
            str(row["scope"])
            for row in conn.execute(
                text(
                    """
                    SELECT DISTINCT scope
                    FROM memory_records
                    WHERE is_deleted = FALSE
                    """
                )
            )
            .mappings()
            .all()
        ]
        for scope in scopes:
            rows = (
                conn.execute(
                    text(
                        """
                        SELECT id, type, confidence, updated_at, created_at
                        FROM memory_records
                        WHERE is_deleted = FALSE AND scope = :scope
                        ORDER BY confidence ASC, updated_at ASC, created_at ASC
                        """
                    ),
                    {"scope": scope},
                )
                .mappings()
                .all()
            )
            _evict_postgres_excess_rows(
                rows,
                scope=scope,
                max_records=max_records,
                exempt=exempt,
                evicted=evicted,
                removed_edges=removed_edges,
                now_iso=now_iso,
                conn=conn,
            )
    return evicted, removed_edges


def _enforce_sqlite_scope_capacity(
    store: MemoryStore,
    *,
    max_records: int,
    exempt: set[str],
    now_iso: str,
) -> tuple[dict[str, int], list[tuple[str, list[Any]]]]:
    evicted: dict[str, int] = {}
    removed_edges: list[tuple[str, list[Any]]] = []
    with store._connect() as conn:
        conn.execute("BEGIN")
        try:
            scopes = [
                str(row["scope"])
                for row in conn.execute(
                    "SELECT DISTINCT scope FROM memory_records WHERE is_deleted = 0"
                ).fetchall()
            ]
            for scope in scopes:
                rows = conn.execute(
                    """
                    SELECT id, type, confidence, updated_at
                    FROM memory_records
                    WHERE is_deleted = 0 AND scope = ?
                    ORDER BY confidence ASC, updated_at ASC, created_at ASC
                    """,
                    (scope,),
                ).fetchall()
                _evict_sqlite_excess_rows(
                    rows,
                    scope=scope,
                    max_records=max_records,
                    exempt=exempt,
                    evicted=evicted,
                    removed_edges=removed_edges,
                    now_iso=now_iso,
                    conn=conn,
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return evicted, removed_edges


def _evict_postgres_excess_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    scope: str,
    max_records: int,
    exempt: set[str],
    evicted: dict[str, int],
    removed_edges: list[tuple[str, list[Any]]],
    now_iso: str,
    conn: Any,
) -> None:
    if len(rows) <= int(max_records):
        return
    active_count = len(rows)
    removable = [row for row in rows if str(row.get("type") or "") not in exempt]
    while active_count > int(max_records) and removable:
        row = removable.pop(0)
        record_id = str(row["id"])
        removed_edges.append(
            (
                record_id,
                soft_delete_postgres_record(conn, record_id, now_iso=now_iso),
            )
        )
        evicted[scope] = evicted.get(scope, 0) + 1
        active_count -= 1


def _evict_sqlite_excess_rows(
    rows: Sequence[SQLiteRow],
    *,
    scope: str,
    max_records: int,
    exempt: set[str],
    evicted: dict[str, int],
    removed_edges: list[tuple[str, list[Any]]],
    now_iso: str,
    conn: Any,
) -> None:
    if len(rows) <= int(max_records):
        return
    active_count = len(rows)
    removable = [row for row in rows if str(row["type"] or "") not in exempt]
    while active_count > int(max_records) and removable:
        row = removable.pop(0)
        record_id = str(row["id"])
        removed_edges.append(
            (
                record_id,
                soft_delete_sqlite_record(conn, record_id, now_iso=now_iso),
            )
        )
        evicted[scope] = evicted.get(scope, 0) + 1
        active_count -= 1
