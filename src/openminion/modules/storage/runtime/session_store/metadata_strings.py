"""Rows of `session_metadata_strings`: the top-level string members of session metadata.

PostgreSQL metadata filters read these rows because PostgreSQL cannot parse every
document `metadata_json` stores (NaN, Infinity, an escaped NUL or lone surrogate).
The rows are written in the session row's transaction on both backends. Filter
semantics per backend: docs/runtime-database-upgrades.md.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from .constants import SESSION_METADATA_STRINGS_BACKFILL_BATCH_SIZE
from .rows import parse_json_object

if TYPE_CHECKING:
    from openminion.modules.storage.runtime.migrations import MigrationRows

    from .backend import RuntimeSessionStoreBackend

# Columns of `session_metadata_strings` as runtime migration 15 creates them.
# The migration backfill reads and writes exactly these, so a column a later
# migration adds cannot break an upgrade that runs migration 15.
_MIGRATION_15_COLUMNS = ("session_id", "key", "value", "entry_digest")
# Rows per INSERT statement of a metadata write: 4 parameters each, within
# SQLite's lowest default limit of 999 bound parameters.
_INSERT_CHUNK = 200


def encode_member(text: str) -> str:
    """Return the stored form of a member name or string value: its JSON text.

    `json.dumps` text is ASCII without a NUL byte, so NUL, lone surrogates and
    non-ASCII text are stored exactly.
    """
    return json.dumps(text)


def entry_digest(key: str, value: str) -> str:
    """Return the indexed digest of an encoded name and value: 16 hex characters.

    Both are JSON string texts, which delimit themselves, so the concatenation
    is unambiguous. The digest only narrows the index lookup.
    """
    return hashlib.sha256((key + value).encode("ascii")).hexdigest()[:16]


def metadata_string_rows(session_id: str, stored_metadata: str) -> list[dict[str, Any]]:
    """Return the rows of one session's stored `metadata_json`.

    Raises `ValueError` when `json.loads` cannot read it.
    """
    rows: list[dict[str, Any]] = []
    for name, value in parse_json_object(stored_metadata).items():
        if not isinstance(value, str):
            continue
        key = encode_member(name)
        encoded = encode_member(value)
        rows.append(
            {
                "session_id": session_id,
                "key": key,
                "value": encoded,
                "entry_digest": entry_digest(key, encoded),
            }
        )
    return rows


def metadata_string_match(key: str, value: str) -> tuple[str, list[str]]:
    """Return the PostgreSQL condition for one filter and its parameters.

    `COLLATE "C"` compares the JSON texts byte for byte, whatever the database
    collation; the digest only selects candidate rows.
    """
    encoded_key = encode_member(key)
    encoded_value = encode_member(value)
    return (
        "EXISTS (SELECT 1 FROM session_metadata_strings AS metadata_string "
        "WHERE metadata_string.session_id = sessions.id "
        "AND metadata_string.entry_digest = ? "
        'AND metadata_string.key = CAST(? AS TEXT) COLLATE "C" '
        'AND metadata_string.value = CAST(? AS TEXT) COLLATE "C")',
        [entry_digest(encoded_key, encoded_value), encoded_key, encoded_value],
    )


def insert_metadata_strings(
    backend: RuntimeSessionStoreBackend, session_id: str, stored_metadata: str
) -> None:
    """Insert the rows of a session's stored metadata, in the caller's transaction."""
    rows = metadata_string_rows(session_id, stored_metadata)
    for start in range(0, len(rows), _INSERT_CHUNK):
        chunk = rows[start : start + _INSERT_CHUNK]
        backend.execute_count(
            "INSERT INTO session_metadata_strings "
            "(session_id, key, value, entry_digest) VALUES "
            + ", ".join("(?, ?, ?, ?)" for _ in chunk),
            [row[column] for row in chunk for column in _MIGRATION_15_COLUMNS],
        )


def replace_metadata_strings(
    backend: RuntimeSessionStoreBackend, session_id: str, stored_metadata: str
) -> None:
    """Replace a session's rows after its `metadata_json` was updated.

    Runs in the transaction of that update, after it: the update locks the
    session row, so concurrent writers to one session replace its rows one at
    a time, and the rows always match the committed `metadata_json`.
    """
    backend.execute_count(
        "DELETE FROM session_metadata_strings WHERE session_id = ?", (session_id,)
    )
    insert_metadata_strings(backend, session_id, stored_metadata)


def backfill_session_metadata_strings(rows: MigrationRows) -> None:
    """Write the rows of every stored session, for runtime migration 15.

    Runs inside the migration's transaction and reads the sessions in id
    order, `SESSION_METADATA_STRINGS_BACKFILL_BATCH_SIZE` (1,000) at a time.
    It only inserts rows; stored metadata is not changed. A session whose
    `metadata_json` `json.loads` cannot read raises `ValueError` naming it, and
    the migration rolls back as a whole; reading that session raises too.
    """
    after_id = ""
    while True:
        batch = rows.query_dicts(
            "SELECT id, metadata_json FROM sessions WHERE id > ? ORDER BY id ASC LIMIT ?",
            (after_id, SESSION_METADATA_STRINGS_BACKFILL_BATCH_SIZE),
        )
        if not batch:
            return
        facts: list[dict[str, Any]] = []
        for row in batch:
            session_id = str(row["id"])
            try:
                session_rows = metadata_string_rows(
                    session_id, str(row["metadata_json"])
                )
            except ValueError as exc:
                raise ValueError(
                    f"session {session_id!r} has a metadata_json that is not JSON: {exc}"
                ) from exc
            facts.extend(
                {column: fact[column] for column in _MIGRATION_15_COLUMNS}
                for fact in session_rows
            )
        if facts:
            rows.insert_many("session_metadata_strings", facts)
        after_id = str(batch[-1]["id"])
