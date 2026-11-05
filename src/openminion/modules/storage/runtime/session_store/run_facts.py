"""Run-status facts of each runtime session event, one `event_run_facts` row each.

The run-status readers in `openminion.modules.task.run` aggregate these rows in
SQL instead of parsing `payload_json`, which Postgres and SQLite cannot always
parse (NaN, Infinity, an escaped NUL). The rows are written in the event's
transaction from the payload as the readers see it (`row_to_event`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .constants import EVENT_RUN_FACTS_BACKFILL_BATCH_SIZE
from .models import EventRecord
from .rows import row_to_event

if TYPE_CHECKING:
    from openminion.modules.storage.runtime.migrations import MigrationRows

RUN_STATES = frozenset(
    {
        "queued",
        "running",
        "waiting_tool",
        "responding",
        "completed",
        "failed",
        "cancelled",
    }
)
DEFAULT_RUN_STATE = "running"
# What the runtime migration 13 backfill reads and writes: the event columns
# and the facts columns of that version, so that a column a later migration
# adds cannot break an upgrade that runs migration 13.
_MIGRATION_13_EVENT_COLUMNS = "id, session_id, event_type, payload_json, created_at"
_MIGRATION_13_FACT_COLUMNS = (
    "event_id",
    "session_id",
    "run_key",
    "run_key_digest",
    "run_state",
    "has_step",
    "has_error",
    "conversation_key",
    "thread_key",
    "has_invocation",
    "has_attach",
    "attach_role",
)


def payload_text(payload: Mapping[str, Any], key: str) -> str:
    """Read a payload value as the readers do: `str(payload.get(key, "")).strip()`."""
    return str(payload.get(key, "")).strip()


def payload_id(payload: Mapping[str, Any], key: str) -> str:
    """Read a conversation or thread id; a false value (null, false, 0) is empty."""
    return str(payload.get(key, "") or "").strip()


def normalize_run_state(*, state: Any, source_event_type: str) -> str:
    candidate = str(state or "").strip().lower()
    if candidate in RUN_STATES:
        return candidate

    if source_event_type.startswith("run."):
        suffix = source_event_type.split(".", 1)[1].strip().lower()
        if suffix in RUN_STATES:
            return suffix
    return DEFAULT_RUN_STATE


def extract_event_error(payload: Mapping[str, Any]) -> str:
    raw_error = payload.get("error")
    return "" if raw_error is None else str(raw_error).strip()


def encode_key(value: str) -> str:
    """Return the stored key of an id: its JSON string, or "" for an empty id.

    JSON text is ASCII without NUL, so equality is exact on both backends; the
    readers order runs by the decoded id.
    """
    return json.dumps(value) if value else ""


def decode_key(key: str) -> str:
    return str(json.loads(key)) if key else ""


def key_digest(key: str) -> str:
    """Return the indexed digest of a key: 16 hex characters, or "" for none.

    The fixed length keeps the index from limiting the length of run ids.
    """
    return hashlib.sha256(key.encode("ascii")).hexdigest()[:16] if key else ""


def event_run_facts(event: EventRecord) -> dict[str, Any]:
    """Return the facts of one stored event.

    - `run_key`, `run_state`, `has_step`, `has_error`: the event's run id,
      state, step and error as `_to_run_event` and `_extract_event_error` read
      them. They are set only when the run id is not empty, as `_to_run_event`
      returns no run event otherwise.
    - `conversation_key`, `thread_key`: the ids `_event_matches_thread` reads.
    - `has_invocation`: a non-empty `invocation_id`, as an invocation start
      needs.
    - `has_attach`, `attach_role`: a non-empty `attach_id`, and the
      `attach_role` lowercased, as `_resolve_writer_attach_id` reads them.
    """
    payload = event.payload
    run_key = encode_key(payload_text(payload, "run_id"))
    has_run = bool(run_key)
    run_state = (
        normalize_run_state(
            state=payload.get("state"), source_event_type=event.event_type
        )
        if has_run
        else ""
    )
    return {
        "event_id": int(event.id),
        "session_id": event.session_id,
        "run_key": run_key,
        "run_key_digest": key_digest(run_key),
        "run_state": run_state,
        "has_step": int(has_run and bool(payload_text(payload, "step"))),
        "has_error": int(has_run and bool(extract_event_error(payload))),
        "conversation_key": encode_key(payload_id(payload, "conversation_id")),
        "thread_key": encode_key(payload_id(payload, "thread_id")),
        "has_invocation": int(bool(payload_text(payload, "invocation_id"))),
        "has_attach": int(bool(payload_text(payload, "attach_id"))),
        "attach_role": encode_key(payload_text(payload, "attach_role").lower()),
    }


def backfill_event_run_facts(rows: MigrationRows) -> None:
    """Write the facts of every stored event, for runtime migration 13.

    Runs inside the migration's transaction and reads the events in id order,
    `EVENT_RUN_FACTS_BACKFILL_BATCH_SIZE` (1,000) at a time. It only inserts
    facts; stored events are not changed. An event whose `payload_json`
    `json.loads` cannot read raises `ValueError` naming it, and the migration
    rolls back as a whole; the readers raise on such an event too.
    """
    after_id = 0
    while True:
        batch = rows.query_dicts(
            f"SELECT {_MIGRATION_13_EVENT_COLUMNS} FROM events "
            "WHERE id > ? ORDER BY id ASC LIMIT ?",
            (after_id, EVENT_RUN_FACTS_BACKFILL_BATCH_SIZE),
        )
        if not batch:
            return
        facts = []
        for row in batch:
            try:
                event = row_to_event(row)
            except ValueError as exc:
                raise ValueError(
                    f"event {row['id']} has a payload_json that is not JSON: {exc}"
                ) from exc
            event_facts = event_run_facts(event)
            facts.append({key: event_facts[key] for key in _MIGRATION_13_FACT_COLUMNS})
        rows.insert_many("event_run_facts", facts)
        after_id = int(batch[-1]["id"])
