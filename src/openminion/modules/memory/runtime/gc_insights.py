import datetime
from typing import Any

from sqlalchemy import text

from openminion.base.time import utc_now
from openminion.modules.memory.runtime.gc_records import (
    parse_when,
    remove_collected_artifact_refs,
    soft_delete_postgres_record,
    soft_delete_sqlite_record,
)
from openminion.modules.memory.storage.base import MemoryStore
from openminion.modules.memory.storage.postgres.store import PostgresMemoryStore


def evict_stale_insights(
    store: MemoryStore,
    *,
    staleness_days: int,
) -> int:
    cutoff = utc_now() - datetime.timedelta(days=max(1, int(staleness_days)))
    now_iso = utc_now().isoformat()

    if isinstance(store, PostgresMemoryStore):
        evicted, removed_edges = _evict_postgres_stale_insights(
            store,
            cutoff=cutoff,
            now_iso=now_iso,
        )
        remove_collected_artifact_refs(store, removed_edges)
        return evicted

    evicted, removed_edges = _evict_sqlite_stale_insights(
        store,
        cutoff=cutoff,
        now_iso=now_iso,
    )
    remove_collected_artifact_refs(store, removed_edges)
    return evicted


def _last_seen(row: Any, *, postgres: bool) -> datetime.datetime | None:
    if postgres:
        return (
            parse_when(str(row.get("last_hit_at") or ""))
            or parse_when(str(row.get("created_at") or ""))
            or parse_when(str(row.get("updated_at") or ""))
        )
    return (
        parse_when(str(row["last_hit_at"] or ""))
        or parse_when(str(row["created_at"] or ""))
        or parse_when(str(row["updated_at"] or ""))
    )


def _evict_postgres_stale_insights(
    store: PostgresMemoryStore,
    *,
    cutoff: datetime.datetime,
    now_iso: str,
) -> tuple[int, list[tuple[str, list[Any]]]]:
    evicted = 0
    removed_edges: list[tuple[str, list[Any]]] = []
    with store.gc_connection() as conn:
        rows = (
            conn.execute(
                text(
                    """
                    SELECT id, created_at, updated_at, last_hit_at
                    FROM memory_records
                    WHERE is_deleted = FALSE AND type = 'meta_insight'
                    """
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            last_seen = _last_seen(row, postgres=True)
            if last_seen is None or last_seen >= cutoff:
                continue
            record_id = str(row["id"])
            removed_edges.append(
                (record_id, soft_delete_postgres_record(conn, record_id, now_iso=now_iso))
            )
            evicted += 1
    return evicted, removed_edges


def _evict_sqlite_stale_insights(
    store: MemoryStore,
    *,
    cutoff: datetime.datetime,
    now_iso: str,
) -> tuple[int, list[tuple[str, list[Any]]]]:
    evicted = 0
    removed_edges: list[tuple[str, list[Any]]] = []
    with store._connect() as conn:
        conn.execute("BEGIN")
        try:
            rows = conn.execute(
                """
                SELECT id, created_at, updated_at, last_hit_at
                FROM memory_records
                WHERE is_deleted = 0 AND type = 'meta_insight'
                """
            ).fetchall()
            for row in rows:
                last_seen = _last_seen(row, postgres=False)
                if last_seen is None or last_seen >= cutoff:
                    continue
                record_id = str(row["id"])
                removed_edges.append(
                    (record_id, soft_delete_sqlite_record(conn, record_id, now_iso=now_iso))
                )
                evicted += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return evicted, removed_edges
