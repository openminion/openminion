import sqlite3
from ...errors import NotFoundError


def apply_supersession(
    conn: sqlite3.Connection,
    *,
    old_record_id: str,
    new_record_id: str,
    now_iso: str,
    valid_to_iso: str,
    reason: str = "",
) -> None:
    """Mark `old_record_id` as superseded by `new_record_id`."""
    conn.execute(
        """
        UPDATE memory_records
        SET superseded_by_id = ?, supersession_reason = ?, valid_to = ?, is_deleted = 1, updated_at = ?
        WHERE id = ?
        """,
        (new_record_id, reason or None, valid_to_iso, now_iso, old_record_id),
    )
    conn.execute(
        """
        UPDATE memory_records
        SET supersedes_id = ?, superseded_by_id = NULL, supersession_reason = NULL, is_deleted = 0, updated_at = ?
        WHERE id = ?
        """,
        (old_record_id, now_iso, new_record_id),
    )
    conn.execute("DELETE FROM memory_fts WHERE id = ?", (old_record_id,))


def get_required_record(
    conn: sqlite3.Connection,
    record_id: str,
) -> sqlite3.Row:
    """Fetch a record row by id, raising `ValueError` if absent."""
    row = conn.execute(
        "SELECT * FROM memory_records WHERE id = ?",
        (record_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"record not found: {record_id}")
    return row


__all__ = ["apply_supersession", "get_required_record"]
