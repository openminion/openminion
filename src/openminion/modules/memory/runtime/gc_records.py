import datetime
import sqlite3
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from openminion.modules.memory.runtime.purge import (
    decode_evidence_ref_values,
    remove_artifact_refs,
)
from openminion.modules.memory.storage.base import MemoryStore


def parse_when(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return None


def soft_delete_sqlite_record(
    conn: sqlite3.Connection,
    record_id: str,
    *,
    now_iso: str,
) -> list[Any]:
    row = conn.execute(
        "SELECT evidence_json FROM memory_records WHERE id = ?",
        (record_id,),
    ).fetchone()
    conn.execute(
        "UPDATE memory_records SET is_deleted = 1, updated_at = ? WHERE id = ?",
        (now_iso, record_id),
    )
    conn.execute("DELETE FROM memory_fts WHERE id = ?", (record_id,))
    if row is None:
        return []
    return decode_evidence_ref_values(row["evidence_json"])


def soft_delete_postgres_record(
    conn: Connection,
    record_id: str,
    *,
    now_iso: str,
) -> list[Any]:
    row = (
        conn.execute(
            text("SELECT evidence_json FROM memory_records WHERE id = :id"),
            {"id": record_id},
        )
        .mappings()
        .first()
    )
    conn.execute(
        text(
            """
            UPDATE memory_records
               SET is_deleted = TRUE,
                   updated_at = :updated_at,
                   search_text = ''
             WHERE id = :id
            """
        ),
        {"updated_at": now_iso, "id": record_id},
    )
    if row is None:
        return []
    return decode_evidence_ref_values(row.get("evidence_json"))


def remove_collected_artifact_refs(
    store: MemoryStore,
    removed_edges: list[tuple[str, list[Any]]],
) -> None:
    for record_id, ref_values in removed_edges:
        remove_artifact_refs(store, owner_id=record_id, ref_values=ref_values)
