from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...models import MemoryCandidate, MemoryRecord
from .sql import _build_search_text, _json_loads

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _find_target_key_collision(
    store: Any,
    connection: Connection,
    candidate: MemoryCandidate,
    target_scope: str,
) -> dict[str, Any] | None:
    if not candidate.key:
        return None
    return store._fetchone(
        """
        SELECT id, evidence_json
          FROM memory_records
         WHERE scope = :scope
           AND type = :record_type
           AND key = :key
           AND is_deleted = FALSE
           AND superseded_by_id IS NULL
        """,
        {"scope": target_scope, "record_type": candidate.type, "key": candidate.key},
        connection=connection,
    )


def _load_record(
    store: Any,
    record_id: str,
    *,
    missing_error: Exception,
) -> MemoryRecord:
    row = store._fetchone(
        "SELECT * FROM memory_records WHERE id = :id", {"id": record_id}
    )
    if row is None:
        raise missing_error
    return store._create_record_from_row(row)


def _retarget_superseding_record_key(
    store: Any,
    connection: Connection,
    old_row: dict[str, Any],
    new_row: dict[str, Any],
    now: str,
) -> None:
    new_content = _json_loads(
        new_row.get("content_json"), new_row.get("content_json") or ""
    )
    new_tags = list(_json_loads(new_row.get("tags_json"), []))
    new_entities = list(_json_loads(new_row.get("entities_json"), []))
    store._execute(
        """
        UPDATE memory_records
           SET key = :key,
               updated_at = :updated_at,
               search_text = :search_text
         WHERE id = :id
        """,
        {
            "key": old_row.get("key"),
            "updated_at": now,
            "id": new_row["id"],
            "search_text": _build_search_text(
                scope=str(new_row.get("scope") or ""),
                record_type=new_row["type"],
                key=old_row.get("key"),
                title=new_row.get("title"),
                content=new_content,
                tags=new_tags,
                entities=new_entities,
            ),
        },
        connection=connection,
    )


__all__ = [
    "_find_target_key_collision",
    "_load_record",
    "_retarget_superseding_record_key",
]
