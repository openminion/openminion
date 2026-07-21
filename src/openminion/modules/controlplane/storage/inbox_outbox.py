import uuid
from typing import Any

from openminion.modules.storage.record_store import RecordStore
from .rows import (
    iso_now as _iso_now,
    iso_after as _iso_after,
    iso_ago as _iso_ago,
    json_dump as _json_dump,
)


class InboxOutboxStore:
    """Durable inbox/outbox pipeline operations backed by ``RecordStore``."""

    def __init__(self, record_store: RecordStore) -> None:
        self._rs = record_store

    def _update(self, sql: str, params: tuple[Any, ...]) -> None:
        with self._rs.transaction():
            self._rs.execute_count(sql, params)

    # Inbox

    def enqueue_inbox(
        self,
        *,
        channel: str,
        chat_id: str,
        channel_message_id: str,
        user_id: str,
        payload: dict[str, Any],
        thread_id: str | None = None,
        inbound_id: str | None = None,
    ) -> tuple[str, bool]:
        inbox_id = inbound_id or uuid.uuid4().hex
        now = _iso_now()
        with self._rs.transaction():
            inserted = self._rs.execute_count(
                """
                INSERT INTO cp_inbox(
                    inbox_id, channel, chat_id, channel_message_id, user_id, thread_id,
                    received_at, payload_json, status, error, attempts, next_attempt_at,
                    locked_at, lock_owner
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(channel, chat_id, channel_message_id) DO NOTHING
                """,
                (
                    inbox_id,
                    channel,
                    chat_id,
                    channel_message_id,
                    user_id,
                    thread_id,
                    now,
                    _json_dump(payload),
                    "new",
                    None,
                    0,
                    now,
                    None,
                    None,
                ),
            )
            if int(inserted or 0) == 1:
                return inbox_id, True
            rows = self._rs.query_dicts(
                """
                SELECT inbox_id FROM cp_inbox
                WHERE channel = ? AND chat_id = ? AND channel_message_id = ?
                """,
                (channel, chat_id, channel_message_id),
            )
        return (str(rows[0]["inbox_id"]) if rows else inbox_id, False)

    def claim_inbox(
        self, *, lock_owner: str, reclaim_ttl_s: int = 120
    ) -> dict[str, Any] | None:
        now = _iso_now()
        with self._rs.transaction():
            reclaim_before = _iso_ago(reclaim_ttl_s)
            self._rs.execute_count(
                """
                UPDATE cp_inbox
                SET status='new', lock_owner=NULL, locked_at=NULL
                WHERE status='processing' AND locked_at IS NOT NULL AND locked_at < ?
                """,
                (reclaim_before,),
            )
            rows = self._rs.query_dicts(
                """
                SELECT * FROM cp_inbox
                WHERE status IN ('new', 'failed')
                  AND next_attempt_at <= ?
                ORDER BY received_at ASC
                LIMIT 1
                """,
                (now,),
            )
            if not rows:
                return None
            inbox_id = str(rows[0]["inbox_id"])
            count = self._rs.execute_count(
                """
                UPDATE cp_inbox
                SET status='processing',
                    attempts=attempts+1,
                    lock_owner=?,
                    locked_at=?
                WHERE inbox_id=? AND status IN ('new', 'failed')
                """,
                (lock_owner, now, inbox_id),
            )
            if count != 1:
                return None
            claimed = self._rs.query_dicts(
                "SELECT * FROM cp_inbox WHERE inbox_id = ?",
                (inbox_id,),
            )
        return claimed[0] if claimed else None

    def ack_inbox(self, inbox_id: str) -> None:
        self._update(
            """
            UPDATE cp_inbox
            SET status='done', lock_owner=NULL, locked_at=NULL, error=NULL
            WHERE inbox_id=?
            """,
            (inbox_id,),
        )

    def fail_inbox(self, inbox_id: str, error: str) -> None:
        self._update(
            """
            UPDATE cp_inbox
            SET status='failed', error=?, lock_owner=NULL, locked_at=NULL
            WHERE inbox_id=?
            """,
            (error[:2000], inbox_id),
        )

    def mark_inbox_retry(
        self,
        inbox_id: str,
        *,
        error: str,
        max_attempts: int = 8,
        max_backoff_s: int = 300,
    ) -> str:
        """Record an inbox processing failure and decide retry vs dead-letter."""
        with self._rs.transaction():
            rows = self._rs.query_dicts(
                "SELECT attempts FROM cp_inbox WHERE inbox_id = ?",
                (inbox_id,),
            )
            attempts = int(rows[0]["attempts"] if rows else 0)
            if attempts >= max_attempts:
                self._rs.execute_count(
                    """
                    UPDATE cp_inbox
                    SET status='dead', error=?, lock_owner=NULL, locked_at=NULL
                    WHERE inbox_id=?
                    """,
                    (error[:2000], inbox_id),
                )
                return "dead"
            delay = min(max_backoff_s, 2 ** max(0, attempts - 1))
            self._rs.execute_count(
                """
                UPDATE cp_inbox
                SET status='failed',
                    next_attempt_at=?,
                    error=?,
                    lock_owner=NULL,
                    locked_at=NULL
                WHERE inbox_id=?
                """,
                (_iso_after(delay), error[:2000], inbox_id),
            )
        return "retry"

    def get_inbox(self, inbox_id: str) -> dict[str, Any] | None:
        rows = self._rs.query_dicts(
            "SELECT * FROM cp_inbox WHERE inbox_id = ?",
            (inbox_id,),
        )
        return rows[0] if rows else None

    # Outbox

    def enqueue_outbox(
        self,
        *,
        channel: str,
        chat_id: str,
        payload: dict[str, Any],
        thread_id: str | None = None,
        reply_to: str | None = None,
        outbox_id: str | None = None,
    ) -> str:
        oid = outbox_id or uuid.uuid4().hex
        now = _iso_now()
        with self._rs.transaction():
            self._rs.execute_count(
                """
                INSERT INTO cp_outbox(
                    outbox_id, channel, chat_id, thread_id, reply_to, payload_json,
                    status, created_at, next_attempt_at, attempts, last_error, lock_owner, locked_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(outbox_id) DO UPDATE SET
                    channel=excluded.channel,
                    chat_id=excluded.chat_id,
                    thread_id=excluded.thread_id,
                    reply_to=excluded.reply_to,
                    payload_json=excluded.payload_json,
                    status=excluded.status,
                    created_at=excluded.created_at,
                    next_attempt_at=excluded.next_attempt_at,
                    attempts=excluded.attempts,
                    last_error=excluded.last_error,
                    lock_owner=excluded.lock_owner,
                    locked_at=excluded.locked_at
                """,
                (
                    oid,
                    channel,
                    chat_id,
                    thread_id,
                    reply_to,
                    _json_dump(payload),
                    "pending",
                    now,
                    now,
                    0,
                    None,
                    None,
                    None,
                ),
            )
        return oid

    def claim_outbox(
        self, *, lock_owner: str, reclaim_ttl_s: int = 120
    ) -> dict[str, Any] | None:
        """Claim the next deliverable outbox row, incrementing ``attempts``."""
        now = _iso_now()
        with self._rs.transaction():
            reclaim_before = _iso_ago(reclaim_ttl_s)
            self._rs.execute_count(
                """
                UPDATE cp_outbox
                SET status='pending', lock_owner=NULL, locked_at=NULL
                WHERE status='sending' AND locked_at IS NOT NULL AND locked_at < ?
                """,
                (reclaim_before,),
            )
            rows = self._rs.query_dicts(
                """
                SELECT * FROM cp_outbox
                WHERE status IN ('pending', 'failed')
                  AND next_attempt_at <= ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (now,),
            )
            if not rows:
                return None
            outbox_id = str(rows[0]["outbox_id"])
            count = self._rs.execute_count(
                """
                UPDATE cp_outbox
                SET status='sending',
                    attempts=attempts+1,
                    lock_owner=?,
                    locked_at=?
                WHERE outbox_id=? AND status IN ('pending', 'failed')
                """,
                (lock_owner, now, outbox_id),
            )
            if count != 1:
                return None
            claimed = self._rs.query_dicts(
                "SELECT * FROM cp_outbox WHERE outbox_id = ?",
                (outbox_id,),
            )
        return claimed[0] if claimed else None

    def mark_outbox_sent(self, outbox_id: str) -> None:
        self._update(
            """
            UPDATE cp_outbox
            SET status='sent', lock_owner=NULL, locked_at=NULL, last_error=NULL
            WHERE outbox_id=?
            """,
            (outbox_id,),
        )

    def mark_outbox_retry(
        self,
        outbox_id: str,
        *,
        error: str,
        max_attempts: int = 8,
        max_backoff_s: int = 300,
    ) -> str:
        """Record a delivery failure and decide retry vs dead-letter."""
        with self._rs.transaction():
            rows = self._rs.query_dicts(
                "SELECT attempts FROM cp_outbox WHERE outbox_id = ?",
                (outbox_id,),
            )
            attempts = int(rows[0]["attempts"] if rows else 0)
            if attempts >= max_attempts:
                self._rs.execute_count(
                    """
                    UPDATE cp_outbox
                    SET status='dead', last_error=?, lock_owner=NULL, locked_at=NULL
                    WHERE outbox_id=?
                    """,
                    (error[:2000], outbox_id),
                )
                return "dead"
            delay = min(max_backoff_s, 2 ** max(0, attempts - 1))
            self._rs.execute_count(
                """
                UPDATE cp_outbox
                SET status='failed',
                    next_attempt_at=?,
                    last_error=?,
                    lock_owner=NULL,
                    locked_at=NULL
                WHERE outbox_id=?
                """,
                (_iso_after(delay), error[:2000], outbox_id),
            )
        return "retry"

    def get_outbox(self, outbox_id: str) -> dict[str, Any] | None:
        rows = self._rs.query_dicts(
            "SELECT * FROM cp_outbox WHERE outbox_id = ?",
            (outbox_id,),
        )
        return rows[0] if rows else None
