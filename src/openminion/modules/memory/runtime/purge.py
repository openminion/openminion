import json
from dataclasses import dataclass
from typing import Any

from openminion.base.time import utc_now
from openminion.modules.artifact.refs import remove_reference_edges
from openminion.modules.memory.storage.base import MemoryStore
from openminion.modules.memory.storage.postgres.store import PostgresMemoryStore


@dataclass
class GCResult:
    deleted_records: int = 0
    deleted_candidates: int = 0
    cleaned_fts_rows: int = 0
    cleaned_entity_rows: int = 0
    decayed_records: int = 0
    capacity_evicted_records: int = 0
    compressed_summaries: int = 0


def decode_evidence_ref_values(raw_json: str | None) -> list[Any]:
    payload = json.loads(str(raw_json or "[]"))
    return payload if isinstance(payload, list) else []


def remove_artifact_refs(
    store: MemoryStore,
    *,
    owner_id: str,
    ref_values: list[Any],
) -> None:
    if not ref_values:
        return
    remove_reference_edges(
        artifactctl=store._resolve_artifactctl(),
        owner_type="memory",
        owner_id=owner_id,
        ref_values=ref_values,
    )


def _postgres_purgeable_rows(
    conn: Any,
    *,
    now: str,
    batch_size: int,
) -> dict[str, Any]:
    from sqlalchemy import text

    protected = """
      AND id NOT IN (
          SELECT supersedes_id FROM memory_records
          WHERE supersedes_id IS NOT NULL AND is_deleted = FALSE
      )
      AND id NOT IN (
          SELECT superseded_by_id FROM memory_records
          WHERE superseded_by_id IS NOT NULL AND is_deleted = FALSE
      )
    """
    dead_rows = (
        conn.execute(
            text(
                "SELECT id, evidence_json FROM memory_records "
                f"WHERE is_deleted = TRUE {protected} LIMIT :batch_size"
            ),
            {"batch_size": int(batch_size)},
        )
        .mappings()
        .all()
    )
    expired_rows = (
        conn.execute(
            text(
                "SELECT id, evidence_json FROM memory_records "
                "WHERE expires_at IS NOT NULL AND expires_at < :now "
                f"AND is_deleted = FALSE {protected} LIMIT :batch_size"
            ),
            {"now": now, "batch_size": int(batch_size)},
        )
        .mappings()
        .all()
    )
    purgeable = {str(row["id"]): row for row in dead_rows}
    for row in expired_rows:
        purgeable.setdefault(str(row["id"]), row)
    return purgeable


def _delete_postgres_records(
    conn: Any,
    *,
    record_ids: tuple[str, ...],
    result: GCResult,
) -> None:
    from sqlalchemy import bindparam, text

    if not record_ids:
        return
    records_param = bindparam("record_ids", expanding=True)
    records = {"record_ids": record_ids}
    for column in ("supersedes_id", "superseded_by_id"):
        conn.execute(
            text(
                f"UPDATE memory_records SET {column} = NULL "
                "WHERE (is_deleted = TRUE OR id IN :record_ids) "
                f"AND {column} IN :record_ids"
            ).bindparams(records_param),
            records,
        )
    for statement in (
        "DELETE FROM memory_tier_transitions WHERE record_id IN :record_ids",
        "DELETE FROM memory_relations WHERE source_record_id IN :record_ids "
        "OR target_record_id IN :record_ids",
    ):
        conn.execute(text(statement).bindparams(records_param), records)
    result.cleaned_entity_rows += int(
        conn.execute(
            text(
                "DELETE FROM memory_entities WHERE record_id IN :record_ids"
            ).bindparams(records_param),
            records,
        ).rowcount
        or 0
    )
    result.deleted_records += int(
        conn.execute(
            text("DELETE FROM memory_records WHERE id IN :record_ids").bindparams(
                records_param
            ),
            records,
        ).rowcount
        or 0
    )


def _purge_postgres_soft_deleted(
    store: PostgresMemoryStore,
    *,
    now: str,
    batch_size: int,
    result: GCResult,
) -> None:
    from sqlalchemy import text

    with store.gc_connection() as conn:
        purgeable = _postgres_purgeable_rows(conn, now=now, batch_size=batch_size)
        removed_record_edges = [
            (record_id, decode_evidence_ref_values(row.get("evidence_json")))
            for record_id, row in purgeable.items()
        ]
        _delete_postgres_records(
            conn,
            record_ids=tuple(purgeable),
            result=result,
        )
        candidate_rows = (
            conn.execute(
                text(
                    "SELECT candidate_id, evidence_json FROM memory_candidates "
                    "WHERE status IN ('rejected', 'promoted')"
                )
            )
            .mappings()
            .all()
        )
        result.deleted_candidates = int(
            conn.execute(
                text(
                    "DELETE FROM memory_candidates "
                    "WHERE status IN ('rejected', 'promoted')"
                )
            ).rowcount
            or 0
        )
    for owner_id, ref_values in removed_record_edges:
        remove_artifact_refs(store, owner_id=owner_id, ref_values=ref_values)
    for row in candidate_rows:
        remove_artifact_refs(
            store,
            owner_id=str(row["candidate_id"]),
            ref_values=decode_evidence_ref_values(row.get("evidence_json")),
        )


def _sqlite_purgeable_ids(conn: Any, *, now: str, batch_size: int) -> list[str]:
    protected = """
      AND id NOT IN (
          SELECT supersedes_id FROM memory_records
          WHERE supersedes_id IS NOT NULL AND is_deleted=0
      )
      AND id NOT IN (
          SELECT superseded_by_id FROM memory_records
          WHERE superseded_by_id IS NOT NULL AND is_deleted=0
      )
    """
    dead_ids = [
        row["id"]
        for row in conn.execute(
            f"SELECT id FROM memory_records WHERE is_deleted=1 {protected} LIMIT ?",
            (batch_size,),
        ).fetchall()
    ]
    expired_ids = [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM memory_records "
            "WHERE expires_at IS NOT NULL AND expires_at < ? AND is_deleted=0 "
            f"{protected} LIMIT ?",
            (now, batch_size),
        ).fetchall()
    ]
    return list(dict.fromkeys(dead_ids + expired_ids))


def _delete_sqlite_records(
    conn: Any,
    *,
    record_ids: list[str],
    result: GCResult,
) -> list[tuple[str, list[Any]]]:
    if not record_ids:
        return []
    conn.execute("CREATE TEMP TABLE memory_gc_purge_ids (id TEXT PRIMARY KEY)")
    conn.executemany(
        "INSERT INTO memory_gc_purge_ids(id) VALUES (?)",
        [(record_id,) for record_id in record_ids],
    )
    rows = conn.execute(
        "SELECT id, evidence_json FROM memory_records "
        "WHERE id IN (SELECT id FROM memory_gc_purge_ids)"
    ).fetchall()
    removed_edges = [
        (str(row["id"]), decode_evidence_ref_values(row["evidence_json"]))
        for row in rows
    ]
    for column in ("supersedes_id", "superseded_by_id"):
        conn.execute(
            f"UPDATE memory_records SET {column} = NULL "
            "WHERE (is_deleted = 1 OR id IN (SELECT id FROM memory_gc_purge_ids)) "
            f"AND {column} IN (SELECT id FROM memory_gc_purge_ids)"
        )
    conn.execute(
        "DELETE FROM memory_tier_transitions "
        "WHERE record_id IN (SELECT id FROM memory_gc_purge_ids)"
    )
    conn.execute(
        "DELETE FROM memory_relations WHERE source_record_id IN "
        "(SELECT id FROM memory_gc_purge_ids) OR target_record_id IN "
        "(SELECT id FROM memory_gc_purge_ids)"
    )
    for table, field in (
        ("memory_fts", "cleaned_fts_rows"),
        ("memory_entities", "cleaned_entity_rows"),
        ("memory_records", "deleted_records"),
    ):
        id_column = "record_id" if table == "memory_entities" else "id"
        cursor = conn.execute(
            f"DELETE FROM {table} "
            f"WHERE {id_column} IN (SELECT id FROM memory_gc_purge_ids)"
        )
        setattr(result, field, getattr(result, field) + max(0, cursor.rowcount))
    conn.execute("DROP TABLE memory_gc_purge_ids")
    return removed_edges


def _purge_sqlite_soft_deleted(
    store: MemoryStore,
    *,
    now: str,
    batch_size: int,
    result: GCResult,
) -> None:
    removed_record_edges: list[tuple[str, list[Any]]] = []
    removed_candidate_edges: list[tuple[str, list[Any]]] = []
    with store._connect() as conn:
        conn.execute("BEGIN")
        try:
            removed_record_edges = _delete_sqlite_records(
                conn,
                record_ids=_sqlite_purgeable_ids(
                    conn,
                    now=now,
                    batch_size=batch_size,
                ),
                result=result,
            )
            candidate_rows = conn.execute(
                "SELECT candidate_id, evidence_json FROM memory_candidates "
                "WHERE status IN ('rejected', 'promoted')"
            ).fetchall()
            removed_candidate_edges = [
                (
                    str(row["candidate_id"]),
                    decode_evidence_ref_values(row["evidence_json"]),
                )
                for row in candidate_rows
            ]
            result.deleted_candidates = conn.execute(
                "DELETE FROM memory_candidates WHERE status IN ('rejected', 'promoted')"
            ).rowcount
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
    for owner_id, ref_values in removed_record_edges + removed_candidate_edges:
        remove_artifact_refs(store, owner_id=owner_id, ref_values=ref_values)


def purge_soft_deleted(
    store: MemoryStore,
    batch_size: int = 500,
) -> GCResult:
    now = utc_now().isoformat()
    result = GCResult()
    if isinstance(store, PostgresMemoryStore):
        _purge_postgres_soft_deleted(
            store,
            now=now,
            batch_size=batch_size,
            result=result,
        )
    else:
        _purge_sqlite_soft_deleted(
            store,
            now=now,
            batch_size=batch_size,
            result=result,
        )
    return result
