import uuid
from typing import Any

from openminion.modules.storage.record_store import RecordStore
from .rows import iso_now as _iso_now, json_dump as _json_dump, json_load as _json_load

PRINCIPAL_BINDING_STATUS_ACTIVE = "active"


def _scopes_json(scopes: list[str] | tuple[str, ...] | None) -> str:
    return _json_dump({"scopes": list(scopes or ())})


def _scopes_list(raw: Any) -> list[str]:
    return [
        str(item) for item in _json_load(raw).get("scopes", []) if str(item).strip()
    ]


def _binding_meta(source: str, *, user_id: Any, session_id: Any) -> str:
    return _json_dump({"source": source, "user_id": user_id, "session_id": session_id})


def _existing_created_at(rows: list[dict[str, Any]], fallback: str) -> str:
    return str(rows[0]["created_at"]) if rows else fallback


class PrincipalsStore:
    """Pairing, principal, and channel-subject operations backed by ``RecordStore``."""

    def __init__(
        self,
        record_store: RecordStore,
        *,
        binding_status_active: str = PRINCIPAL_BINDING_STATUS_ACTIVE,
    ) -> None:
        self._rs = record_store
        self._binding_status_active = binding_status_active

    def _upsert_principal_row(
        self,
        principal_id: str,
        *,
        created_at: str,
        updated_at: str,
        meta_json: str | None = None,
    ) -> None:
        if meta_json is None:
            self._rs.execute_count(
                """
                INSERT INTO cp_principals(principal_id, created_at, updated_at, meta_json)
                VALUES (?,?,?,?)
                ON CONFLICT(principal_id) DO UPDATE SET
                    updated_at=excluded.updated_at
                """,
                (principal_id, created_at, updated_at, _json_dump({})),
            )
            return
        self._rs.execute_count(
            """
            INSERT INTO cp_principals(principal_id, created_at, updated_at, meta_json)
            VALUES (?,?,?,?)
            ON CONFLICT(principal_id) DO UPDATE SET
                updated_at=excluded.updated_at,
                meta_json=excluded.meta_json
            """,
            (principal_id, created_at, updated_at, meta_json),
        )

    def _upsert_channel_subject(
        self,
        *,
        principal_id: str,
        channel: str,
        subject_id: str,
        status: str,
        scopes_json: str,
        note: str | None,
        created_at: str,
        last_seen_at: str,
        meta_json: str,
    ) -> None:
        self._rs.execute_count(
            """
            INSERT INTO cp_channel_subjects(
                principal_id, channel, subject_id, status, scopes_json, note,
                created_at, last_seen_at, meta_json
            ) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(channel, subject_id) DO UPDATE SET
                principal_id=excluded.principal_id,
                status=excluded.status,
                scopes_json=excluded.scopes_json,
                note=excluded.note,
                last_seen_at=excluded.last_seen_at,
                meta_json=excluded.meta_json
            """,
            (
                principal_id,
                channel,
                subject_id,
                status,
                scopes_json,
                note,
                created_at,
                last_seen_at,
                meta_json,
            ),
        )

    def upsert_pairing(
        self,
        *,
        channel: str,
        chat_id: str,
        user_id: str,
        session_id: str,
        status: str | None = None,
        scopes: list[str] | tuple[str, ...] | None = None,
        note: str | None = None,
        pairing_id: str | None = None,
    ) -> str:
        active = status or self._binding_status_active
        pid = pairing_id or uuid.uuid4().hex
        now = _iso_now()
        scopes_json = _scopes_json(scopes)
        with self._rs.transaction():
            self._rs.execute_count(
                """
                INSERT INTO cp_pairings(
                    pairing_id, channel, chat_id, user_id, session_id,
                    created_at, last_seen_at, status, scopes_json, note
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(channel, chat_id) DO UPDATE SET
                    user_id=excluded.user_id,
                    session_id=excluded.session_id,
                    last_seen_at=excluded.last_seen_at,
                    status=excluded.status,
                    scopes_json=excluded.scopes_json,
                    note=excluded.note
                """,
                (
                    pid,
                    channel,
                    chat_id,
                    user_id,
                    session_id,
                    now,
                    now,
                    active,
                    scopes_json,
                    note,
                ),
            )
            rows = self._rs.query_dicts(
                "SELECT pairing_id FROM cp_pairings WHERE channel = ? AND chat_id = ?",
                (channel, chat_id),
            )
            resolved_pid = str(rows[0]["pairing_id"]) if rows else pid

            # CTP3-05 compatibility dual-write
            self._upsert_principal_row(
                resolved_pid,
                created_at=now,
                updated_at=now,
            )
            existing = self._rs.query_dicts(
                "SELECT created_at FROM cp_channel_subjects WHERE channel = ? AND subject_id = ?",
                (channel, chat_id),
            )
            created_at = _existing_created_at(existing, now)
            self._upsert_channel_subject(
                principal_id=resolved_pid,
                channel=channel,
                subject_id=chat_id,
                status=active,
                scopes_json=scopes_json,
                note=note,
                created_at=created_at,
                last_seen_at=now,
                meta_json=_binding_meta(
                    "cp_pairings_dual_write",
                    user_id=user_id,
                    session_id=session_id,
                ),
            )
        return resolved_pid

    def get_pairing(self, *, channel: str, chat_id: str) -> dict[str, Any] | None:
        rows = self._rs.query_dicts(
            """
            SELECT pairing_id, channel, chat_id, user_id, session_id, created_at,
                   last_seen_at, status, scopes_json, note
            FROM cp_pairings
            WHERE channel = ? AND chat_id = ? AND status = ?
            """,
            (channel, chat_id, self._binding_status_active),
        )
        if not rows:
            return None
        out = rows[0]
        out["scopes"] = _scopes_list(out.get("scopes_json"))
        return out

    def list_pairings(
        self, *, channel: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        max_items = max(1, int(limit))
        params: list[Any] = [self._binding_status_active]
        where = ["status = ?"]
        if channel:
            where.append("channel = ?")
            params.append(channel)
        params.append(max_items)
        rows = self._rs.query_dicts(
            f"""
            SELECT pairing_id, channel, chat_id, user_id, session_id, created_at,
                   last_seen_at, status, scopes_json, note
            FROM cp_pairings
            WHERE {" AND ".join(where)}
            ORDER BY last_seen_at DESC
            LIMIT ?
            """,
            tuple(params),
        )
        for row in rows:
            row["scopes"] = _scopes_list(row.get("scopes_json"))
        return rows

    def touch_pairing(self, *, channel: str, chat_id: str) -> None:
        now = _iso_now()
        with self._rs.transaction():
            self._rs.execute_count(
                """
                UPDATE cp_pairings
                SET last_seen_at = ?
                WHERE channel = ? AND chat_id = ? AND status = ?
                """,
                (now, channel, chat_id, self._binding_status_active),
            )
            self._rs.execute_count(
                """
                UPDATE cp_channel_subjects
                SET last_seen_at = ?
                WHERE channel = ? AND subject_id = ?
                """,
                (now, channel, chat_id),
            )

    def backfill_pairings_to_principals(
        self,
        *,
        channel: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> dict[str, int]:
        active = status or self._binding_status_active
        where = ["status = ?"]
        params: list[Any] = [active]
        if channel is not None:
            where.append("channel = ?")
            params.append(channel)
        limit_clause = ""
        if limit is not None:
            limit_clause = " LIMIT ?"
            params.append(max(1, int(limit)))

        with self._rs.transaction():
            rows = self._rs.query_dicts(
                f"""
                SELECT pairing_id, channel, chat_id, user_id, session_id, status,
                       scopes_json, note, created_at, last_seen_at
                FROM cp_pairings
                WHERE {" AND ".join(where)}
                ORDER BY created_at ASC
                {limit_clause}
                """,
                params,
            )
            scanned = 0
            principal_new = 0
            subject_new = 0
            subject_updated = 0
            for pairing in rows:
                scanned += 1
                principal_id = str(pairing["pairing_id"])
                channel_id = str(pairing["channel"])
                chat_id = str(pairing["chat_id"])
                principal_exists = self._rs.query_dicts(
                    "SELECT 1 FROM cp_principals WHERE principal_id = ?",
                    (principal_id,),
                )
                if not principal_exists:
                    principal_new += 1
                self._upsert_principal_row(
                    principal_id,
                    created_at=str(pairing.get("created_at") or _iso_now()),
                    updated_at=_iso_now(),
                )
                existing = self._rs.query_dicts(
                    "SELECT created_at FROM cp_channel_subjects WHERE channel = ? AND subject_id = ?",
                    (channel_id, chat_id),
                )
                if not existing:
                    subject_new += 1
                else:
                    subject_updated += 1
                created_at = _existing_created_at(
                    existing,
                    str(pairing.get("created_at") or _iso_now()),
                )
                self._upsert_channel_subject(
                    principal_id=principal_id,
                    channel=channel_id,
                    subject_id=chat_id,
                    status=str(pairing.get("status") or self._binding_status_active),
                    scopes_json=str(pairing.get("scopes_json") or _scopes_json(None)),
                    note=pairing.get("note"),
                    created_at=created_at,
                    last_seen_at=str(pairing.get("last_seen_at") or _iso_now()),
                    meta_json=_binding_meta(
                        "cp_pairings_backfill",
                        user_id=pairing.get("user_id"),
                        session_id=pairing.get("session_id"),
                    ),
                )
        return {
            "scanned": scanned,
            "principal_new": principal_new,
            "subject_new": subject_new,
            "subject_updated": subject_updated,
        }

    def upsert_principal(
        self,
        *,
        principal_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        pid = str(principal_id or uuid.uuid4().hex).strip()
        if not pid:
            raise ValueError("principal_id must be non-empty")
        now = _iso_now()
        with self._rs.transaction():
            self._upsert_principal_row(
                pid,
                created_at=now,
                updated_at=now,
                meta_json=None if meta is None else _json_dump(meta),
            )
        return pid

    def bind_principal_subject(
        self,
        *,
        principal_id: str,
        channel: str,
        subject_id: str,
        status: str | None = None,
        scopes: list[str] | tuple[str, ...] | None = None,
        note: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        active = status or self._binding_status_active
        pid = str(principal_id or "").strip()
        chan = str(channel or "").strip()
        sub = str(subject_id or "").strip()
        if not pid:
            raise ValueError("principal_id must be non-empty")
        if not chan or not sub:
            raise ValueError("channel and subject_id must be non-empty")
        now = _iso_now()
        with self._rs.transaction():
            principal_row = self._rs.query_dicts(
                "SELECT principal_id FROM cp_principals WHERE principal_id = ?",
                (pid,),
            )
            if not principal_row:
                raise ValueError(f"principal_id not found: {pid}")
            existing = self._rs.query_dicts(
                "SELECT created_at FROM cp_channel_subjects WHERE channel = ? AND subject_id = ?",
                (chan, sub),
            )
            created_at = _existing_created_at(existing, now)
            self._upsert_channel_subject(
                principal_id=pid,
                channel=chan,
                subject_id=sub,
                status=str(active),
                scopes_json=_scopes_json(scopes),
                note=note,
                created_at=created_at,
                last_seen_at=now,
                meta_json=_json_dump(meta),
            )

    def resolve_principal(self, *, channel: str, subject_id: str) -> str | None:
        chan = str(channel or "").strip()
        sub = str(subject_id or "").strip()
        if not chan or not sub:
            return None
        rows = self._rs.query_dicts(
            """
            SELECT principal_id
            FROM cp_channel_subjects
            WHERE channel = ? AND subject_id = ? AND status = ?
            """,
            (chan, sub, self._binding_status_active),
        )
        if not rows:
            return None
        principal_id = str(rows[0]["principal_id"] or "").strip()
        return principal_id or None

    def get_channel_subject(
        self, *, channel: str, subject_id: str
    ) -> dict[str, Any] | None:
        chan = str(channel or "").strip()
        sub = str(subject_id or "").strip()
        if not chan or not sub:
            return None
        rows = self._rs.query_dicts(
            """
            SELECT principal_id, channel, subject_id, status, scopes_json, note,
                   created_at, last_seen_at, meta_json
            FROM cp_channel_subjects
            WHERE channel = ? AND subject_id = ?
            """,
            (chan, sub),
        )
        if not rows:
            return None
        out = rows[0]
        out["scopes"] = _scopes_list(out.get("scopes_json"))
        out["meta"] = _json_load(out.get("meta_json"))
        return out

    def touch_channel_subject(self, *, channel: str, subject_id: str) -> None:
        chan = str(channel or "").strip()
        sub = str(subject_id or "").strip()
        if not chan or not sub:
            return
        with self._rs.transaction():
            self._rs.execute_count(
                """
                UPDATE cp_channel_subjects
                SET last_seen_at = ?
                WHERE channel = ? AND subject_id = ?
                """,
                (_iso_now(), chan, sub),
            )
