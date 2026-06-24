from __future__ import annotations

from uuid import uuid4

from ..pinned_context import (
    DEFAULT_PINNED_CONTEXT_POLICY,
    PinnedContextEntry,
    PinnedContextPolicy,
    decode_pinned_context,
    encode_pinned_context,
    normalize_pin_entries,
)
from .backend import RuntimeSessionStoreBackend
from .keys import utc_now_iso
from .models import SessionContextRecord
from .rows import (
    build_session_context_update_values,
    normalize_optional_text,
    row_to_session_context,
)


class RuntimeSessionStoreContext:
    def __init__(self, backend: RuntimeSessionStoreBackend) -> None:
        self._backend = backend

    def get_session_context(self, *, session_id: str) -> SessionContextRecord | None:
        row = self._backend.query_one(
            """
            SELECT
                session_id,
                pinned_context,
                summary_short,
                rolling_summary,
                compacted_until_rowid,
                compacted_until_created_at,
                compacted_until_message_id,
                compacted_message_count,
                version,
                created_at,
                updated_at
            FROM session_contexts
            WHERE session_id = ?
            """,
            (session_id,),
        )
        if row is None:
            return None
        return row_to_session_context(row)

    def list_pins(self, *, session_id: str) -> list[PinnedContextEntry]:
        context = self.ensure_session_context(session_id=session_id)
        return decode_pinned_context(context.pinned_context)

    def replace_pins(
        self,
        *,
        session_id: str,
        pins: list[PinnedContextEntry],
        policy: PinnedContextPolicy = DEFAULT_PINNED_CONTEXT_POLICY,
    ) -> SessionContextRecord:
        encoded = encode_pinned_context(pins, policy=policy)
        return self.update_session_context(
            session_id=session_id,
            pinned_context=encoded,
        )

    def add_pin(
        self,
        *,
        session_id: str,
        source: str,
        text: str,
        pin_id: str | None = None,
        created_at: str | None = None,
        policy: PinnedContextPolicy = DEFAULT_PINNED_CONTEXT_POLICY,
    ) -> list[PinnedContextEntry]:
        existing = self.list_pins(session_id=session_id)
        existing.append(
            PinnedContextEntry(
                pin_id=str(pin_id or "").strip() or f"pin-{uuid4().hex[:8]}",
                source=source,
                text=text,
                created_at=str(created_at or utc_now_iso()).strip(),
            )
        )
        normalized = normalize_pin_entries(existing, policy=policy)
        self.replace_pins(session_id=session_id, pins=normalized, policy=policy)
        return self.list_pins(session_id=session_id)

    def remove_pin(
        self,
        *,
        session_id: str,
        pin_id: str | None = None,
        text: str | None = None,
        source: str | None = None,
        policy: PinnedContextPolicy = DEFAULT_PINNED_CONTEXT_POLICY,
    ) -> list[PinnedContextEntry]:
        pins = self.list_pins(session_id=session_id)
        pin_id_value = normalize_optional_text(pin_id)
        text_value = normalize_optional_text(text)
        source_value = normalize_optional_text(source).lower()
        if not pin_id_value and not text_value:
            raise ValueError("remove_pin requires pin_id or text")

        filtered: list[PinnedContextEntry] = []
        for entry in pins:
            remove_entry = False
            if pin_id_value and entry.pin_id == pin_id_value:
                remove_entry = True
            elif text_value and entry.text.strip().lower() == text_value.lower():
                if source_value:
                    remove_entry = entry.source.strip().lower() == source_value
                else:
                    remove_entry = True
            if not remove_entry:
                filtered.append(entry)

        normalized = normalize_pin_entries(filtered, policy=policy)
        self.replace_pins(session_id=session_id, pins=normalized, policy=policy)
        return self.list_pins(session_id=session_id)

    def ensure_session_context(self, *, session_id: str) -> SessionContextRecord:
        existing = self.get_session_context(session_id=session_id)
        if existing is not None:
            return existing

        now = utc_now_iso()
        self._backend.execute_count(
            """
            INSERT INTO session_contexts(
                session_id,
                pinned_context,
                summary_short,
                rolling_summary,
                compacted_until_rowid,
                compacted_until_created_at,
                compacted_until_message_id,
                compacted_message_count,
                version,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, "", "", "", 0, "", "", 0, 1, now, now),
        )
        created = self.get_session_context(session_id=session_id)
        if created is None:
            raise RuntimeError(
                f"Failed to create session context for session_id={session_id}"
            )
        return created

    def update_session_context(
        self,
        *,
        session_id: str,
        pinned_context: str | None = None,
        summary_short: str | None = None,
        rolling_summary: str | None = None,
        compacted_until_rowid: int | None = None,
        compacted_until_created_at: str | None = None,
        compacted_until_message_id: str | None = None,
        compacted_message_count: int | None = None,
        version: int | None = None,
        expected_version: int | None = None,
    ) -> SessionContextRecord:
        current = self.ensure_session_context(session_id=session_id)
        now = utc_now_iso()
        update_values = build_session_context_update_values(
            current=current,
            pinned_context=pinned_context,
            summary_short=summary_short,
            rolling_summary=rolling_summary,
            compacted_until_rowid=compacted_until_rowid,
            compacted_until_created_at=compacted_until_created_at,
            compacted_until_message_id=compacted_until_message_id,
            compacted_message_count=compacted_message_count,
            version=version,
        )
        if expected_version is not None:
            updated = self._backend.execute_count(
                """
                UPDATE session_contexts
                SET
                    pinned_context = ?,
                    summary_short = ?,
                    rolling_summary = ?,
                    compacted_until_rowid = ?,
                    compacted_until_created_at = ?,
                    compacted_until_message_id = ?,
                    compacted_message_count = ?,
                    version = ?,
                    updated_at = ?
                WHERE session_id = ? AND version = ?
                """,
                (*update_values, now, session_id, int(expected_version)),
            )
            if updated == 0:
                refreshed = self.get_session_context(session_id=session_id)
                if refreshed is None:
                    raise RuntimeError(
                        f"Session context disappeared for session_id={session_id}"
                    )
                return refreshed
        else:
            self._backend.execute_count(
                """
                UPDATE session_contexts
                SET
                    pinned_context = ?,
                    summary_short = ?,
                    rolling_summary = ?,
                    compacted_until_rowid = ?,
                    compacted_until_created_at = ?,
                    compacted_until_message_id = ?,
                    compacted_message_count = ?,
                    version = ?,
                    updated_at = ?
                WHERE session_id = ?
                """,
                (*update_values, now, session_id),
            )
        updated_row = self.get_session_context(session_id=session_id)
        if updated_row is None:
            raise RuntimeError(
                f"Failed to update session context for session_id={session_id}"
            )
        return updated_row
