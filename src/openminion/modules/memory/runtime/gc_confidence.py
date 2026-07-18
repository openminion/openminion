import datetime
from typing import Any

from sqlalchemy import text

from openminion.base.time import utc_now
from openminion.modules.memory.models import MemoryScope
from openminion.modules.memory.runtime.gc_records import (
    parse_when,
    remove_collected_artifact_refs,
    soft_delete_postgres_record,
    soft_delete_sqlite_record,
)
from openminion.modules.memory.storage.base import MemoryStore
from openminion.modules.memory.storage.postgres.store import PostgresMemoryStore


def apply_confidence_decay(
    store: MemoryStore,
    *,
    interval_days: int,
    decay_rate: float,
    min_confidence: float,
    disuse_threshold_days: int | None = None,
    disuse_decay_multiplier: float = 1.0,
    exempt_types: list[str] | tuple[str, ...] = ("pin",),
) -> tuple[int, int]:
    now = utc_now()
    cutoff = now - datetime.timedelta(days=max(1, int(interval_days)))
    exempt = {str(item) for item in exempt_types}

    if isinstance(store, PostgresMemoryStore):
        decayed, evicted, removed_edges = _decay_postgres_records(
            store,
            now=now,
            cutoff=cutoff,
            interval_days=interval_days,
            decay_rate=decay_rate,
            min_confidence=min_confidence,
            disuse_threshold_days=disuse_threshold_days,
            disuse_decay_multiplier=disuse_decay_multiplier,
            exempt=exempt,
        )
        remove_collected_artifact_refs(store, removed_edges)
        return decayed, evicted

    decayed, evicted, removed_edges = _decay_sqlite_records(
        store,
        now=now,
        cutoff=cutoff,
        interval_days=interval_days,
        decay_rate=decay_rate,
        min_confidence=min_confidence,
        disuse_threshold_days=disuse_threshold_days,
        disuse_decay_multiplier=disuse_decay_multiplier,
        exempt=exempt,
    )
    remove_collected_artifact_refs(store, removed_edges)
    return decayed, evicted


def _effective_decay(
    *,
    now: datetime.datetime,
    updated_at: datetime.datetime,
    interval_days: int,
    decay_rate: float,
    last_hit_at: datetime.datetime | None,
    disuse_threshold_days: int | None,
    disuse_decay_multiplier: float,
    sqlite_disuse_rule: bool,
) -> float:
    elapsed_seconds = (now - updated_at).total_seconds()
    interval_seconds = max(1, int(interval_days)) * 86400.0
    effective_decay = float(decay_rate) * max(
        1.0, elapsed_seconds / max(1.0, interval_seconds)
    )
    if disuse_threshold_days is None or disuse_decay_multiplier <= 1.0:
        return effective_decay
    disuse_cutoff = now - datetime.timedelta(days=max(1, int(disuse_threshold_days)))
    if sqlite_disuse_rule:
        if last_hit_at is not None and last_hit_at < disuse_cutoff:
            return effective_decay * float(disuse_decay_multiplier)
        return effective_decay
    if last_hit_at is None or last_hit_at < disuse_cutoff:
        return effective_decay * float(disuse_decay_multiplier)
    return effective_decay


def _scope_is_exempt(scope: str, record_type: str, exempt: set[str]) -> bool:
    try:
        parsed_scope = MemoryScope.parse(scope)
    except ValueError:
        parsed_scope = None
    return (parsed_scope is not None and parsed_scope.is_session) or record_type in exempt


def _decay_postgres_records(
    store: PostgresMemoryStore,
    *,
    now: datetime.datetime,
    cutoff: datetime.datetime,
    interval_days: int,
    decay_rate: float,
    min_confidence: float,
    disuse_threshold_days: int | None,
    disuse_decay_multiplier: float,
    exempt: set[str],
) -> tuple[int, int, list[tuple[str, list[Any]]]]:
    decayed = 0
    evicted = 0
    removed_edges: list[tuple[str, list[Any]]] = []
    with store.gc_connection() as conn:
        rows = (
            conn.execute(
                text(
                    """
                    SELECT id, scope, type, confidence, updated_at, last_hit_at
                    FROM memory_records
                    WHERE is_deleted = FALSE
                    """
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            scope = str(row.get("scope") or "")
            record_type = str(row.get("type") or "")
            if _scope_is_exempt(scope, record_type, exempt):
                continue
            updated_at = parse_when(str(row.get("updated_at") or ""))
            if updated_at is None or updated_at >= cutoff:
                continue
            last_hit_at = parse_when(str(row.get("last_hit_at") or ""))
            effective_decay = _effective_decay(
                now=now,
                updated_at=updated_at,
                interval_days=interval_days,
                decay_rate=decay_rate,
                last_hit_at=last_hit_at,
                disuse_threshold_days=disuse_threshold_days,
                disuse_decay_multiplier=disuse_decay_multiplier,
                sqlite_disuse_rule=False,
            )
            new_confidence = max(
                0.0, float(row.get("confidence") or 0.0) - effective_decay
            )
            record_id = str(row["id"])
            conn.execute(
                text(
                    """
                    UPDATE memory_records
                       SET confidence = :confidence,
                           updated_at = :updated_at
                     WHERE id = :id
                    """
                ),
                {"confidence": new_confidence, "updated_at": now.isoformat(), "id": record_id},
            )
            decayed += 1
            if new_confidence < float(min_confidence):
                removed_edges.append(
                    (
                        record_id,
                        soft_delete_postgres_record(conn, record_id, now_iso=now.isoformat()),
                    )
                )
                evicted += 1
    return decayed, evicted, removed_edges


def _decay_sqlite_records(
    store: MemoryStore,
    *,
    now: datetime.datetime,
    cutoff: datetime.datetime,
    interval_days: int,
    decay_rate: float,
    min_confidence: float,
    disuse_threshold_days: int | None,
    disuse_decay_multiplier: float,
    exempt: set[str],
) -> tuple[int, int, list[tuple[str, list[Any]]]]:
    decayed = 0
    evicted = 0
    removed_edges: list[tuple[str, list[Any]]] = []
    with store._connect() as conn:
        conn.execute("BEGIN")
        try:
            rows = conn.execute(
                """
                SELECT id, scope, type, confidence, updated_at, last_hit_at
                FROM memory_records
                WHERE is_deleted = 0
                """
            ).fetchall()
            for row in rows:
                scope = str(row["scope"] or "")
                record_type = str(row["type"] or "")
                if _scope_is_exempt(scope, record_type, exempt):
                    continue
                updated_at = parse_when(str(row["updated_at"] or ""))
                if updated_at is None or updated_at >= cutoff:
                    continue
                last_hit_at = parse_when(str(row["last_hit_at"] or ""))
                effective_decay = _effective_decay(
                    now=now,
                    updated_at=updated_at,
                    interval_days=interval_days,
                    decay_rate=decay_rate,
                    last_hit_at=last_hit_at,
                    disuse_threshold_days=disuse_threshold_days,
                    disuse_decay_multiplier=disuse_decay_multiplier,
                    sqlite_disuse_rule=True,
                )
                new_confidence = max(
                    0.0, float(row["confidence"] or 0.0) - effective_decay
                )
                record_id = str(row["id"])
                conn.execute(
                    "UPDATE memory_records SET confidence = ?, updated_at = ? WHERE id = ?",
                    (new_confidence, now.isoformat(), record_id),
                )
                decayed += 1
                if new_confidence < float(min_confidence):
                    removed_edges.append(
                        (
                            record_id,
                            soft_delete_sqlite_record(conn, record_id, now_iso=now.isoformat()),
                        )
                    )
                    evicted += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return decayed, evicted, removed_edges
