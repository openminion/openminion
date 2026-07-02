import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..constants import PRINCIPAL_BINDING_STATUS_ACTIVE
from ..interfaces import CONTROLPLANE_INTERFACE_VERSION
from ..contracts.models import AttachmentInput, AttachmentRef, InboundMessage
from openminion.modules.storage.runtime.module_store import (
    BaseModuleSQLiteStore,
    BaseModuleStore,
)
from openminion.modules.storage.record_store import RecordStore
from .base import ControlplaneStore
from .inbox_outbox import InboxOutboxStore
from .principals import PrincipalsStore
from .rows import iso_now as _iso_now, json_dump as _json_dump, json_load as _json_load
from .schema import MIGRATIONS as _MIGRATIONS, list_migrations as _list_migrations


def _split_sql_statements(script: str) -> list[str]:
    return [
        statement.strip() for statement in str(script).split(";") if statement.strip()
    ]


def _postgresify_ddl(statement: str) -> str:
    return statement.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")


class _ControlPlaneStoreMixin(ControlplaneStore):
    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def __init_agents(self) -> None:
        self._agents: dict[str, dict[str, str]] = {
            "agent:default": {"id": "agent:default", "name": "Default Agent"},
            "agent:brain": {"id": "agent:brain", "name": "Brain Agent"},
        }
        self._inbox_outbox = InboxOutboxStore(self._record_store)
        self._principals = PrincipalsStore(
            self._record_store,
            binding_status_active=PRINCIPAL_BINDING_STATUS_ACTIVE,
        )

    def _post_store_init(self) -> None:
        self.__init_agents()

    def _list_migrations(self) -> list[str]:
        return _list_migrations()

    def _module_package(self) -> str:
        return __package__

    def _ddl_for_backend(self, statement: str) -> str:
        return statement

    def _query_dicts(
        self, sql: str, params: tuple[Any, ...] | list[Any] | None = None
    ) -> list[dict[str, Any]]:
        return self._record_store.query_dicts(sql, params)

    def _query_one(
        self, sql: str, params: tuple[Any, ...] | list[Any] | None = None
    ) -> dict[str, Any] | None:
        rows = self._query_dicts(sql, params)
        return rows[0] if rows else None

    def _execute_count(
        self, sql: str, params: tuple[Any, ...] | list[Any] | None = None
    ) -> int:
        return self._record_store.execute_count(sql, params)

    def _apply_migrations(self) -> None:
        with self._lock:
            self._execute_count(
                """
                CREATE TABLE IF NOT EXISTS cp_migrations (
                    version    INTEGER PRIMARY KEY,
                    name       TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                int(row["version"])
                for row in self._query_dicts("SELECT version FROM cp_migrations")
            }
            for version, name, sql in _MIGRATIONS:
                if version in applied:
                    continue
                with self._record_store.transaction():
                    for statement in _split_sql_statements(sql):
                        self._execute_count(self._ddl_for_backend(statement))
                    self._execute_count(
                        """
                        INSERT INTO cp_migrations(version, name, applied_at)
                        VALUES (?,?,?)
                        ON CONFLICT(version) DO NOTHING
                        """,
                        (version, name, _iso_now()),
                    )

    def get_chat_binding(self, chat_key: str) -> dict[str, Any] | None:
        return self._query_one(
            """
            SELECT chat_key, session_id, active_agent_id, updated_at
            FROM cp_chat_bindings
            WHERE chat_key = ?
            LIMIT 1
            """,
            (chat_key,),
        )

    def set_chat_binding(
        self, chat_key: str, session_id: str, agent_id: str | None = None
    ) -> None:
        now = _iso_now()
        with self._lock, self._record_store.transaction():
            self._execute_count(
                """
                INSERT INTO cp_chat_bindings(chat_key, session_id, active_agent_id, updated_at)
                VALUES (?,?,?,?)
                ON CONFLICT(chat_key) DO UPDATE SET
                    session_id=excluded.session_id,
                    active_agent_id=excluded.active_agent_id,
                    updated_at=excluded.updated_at
                """,
                (chat_key, session_id, agent_id, now),
            )
            self._ensure_session_locked(session_id, user_key=None, chat_key=chat_key)
            if agent_id:
                self._set_session_agent_locked(session_id, agent_id)

    def resolve_session(self, user_key: str, chat_key: str) -> str:
        binding = self.get_chat_binding(chat_key)
        if binding is not None:
            session_id = str(binding["session_id"])
            with self._lock, self._record_store.transaction():
                self._ensure_session_locked(
                    session_id, user_key=user_key, chat_key=chat_key
                )
            return session_id
        return self.new_session(user_key, chat_key)

    def new_session(self, user_key: str, chat_key: str) -> str:
        session_id = f"sess-{uuid.uuid4().hex[:12]}"
        now = _iso_now()
        with self._lock, self._record_store.transaction():
            self._ensure_session_locked(
                session_id, user_key=user_key, chat_key=chat_key
            )
            self._execute_count(
                """
                INSERT INTO cp_chat_bindings(chat_key, session_id, active_agent_id, updated_at)
                VALUES (?,?,?,?)
                ON CONFLICT(chat_key) DO UPDATE SET
                    session_id=excluded.session_id,
                    active_agent_id=excluded.active_agent_id,
                    updated_at=excluded.updated_at
                """,
                (chat_key, session_id, "agent:default", now),
            )
            self._set_session_agent_locked(session_id, "agent:default")
        return session_id

    def rebind_session(self, user_key: str, chat_key: str) -> str:
        return self.new_session(user_key, chat_key)

    def bind_session(self, user_key: str, chat_key: str, session_id: str) -> None:
        now = _iso_now()
        with self._lock, self._record_store.transaction():
            self._ensure_session_locked(
                session_id, user_key=user_key, chat_key=chat_key
            )
            current = self.resolve_agent(session_id)
            self._execute_count(
                """
                INSERT INTO cp_chat_bindings(chat_key, session_id, active_agent_id, updated_at)
                VALUES (?,?,?,?)
                ON CONFLICT(chat_key) DO UPDATE SET
                    session_id=excluded.session_id,
                    active_agent_id=excluded.active_agent_id,
                    updated_at=excluded.updated_at
                """,
                (chat_key, session_id, current, now),
            )

    def session_owner(self, session_id: str) -> str | None:
        row = self._query_one(
            "SELECT user_key FROM cp_sessions WHERE session_id = ? LIMIT 1",
            (session_id,),
        )
        if row is None:
            return None
        owner = row.get("user_key")
        return str(owner) if owner is not None else None

    def bind_session_owned(
        self,
        *,
        user_key: str,
        chat_key: str,
        session_id: str,
        is_admin: bool,
    ) -> bool:
        owner = self.session_owner(session_id)
        if owner is None:
            return False
        if owner != user_key and not is_admin:
            return False
        self.bind_session(user_key, chat_key, session_id)
        return True

    def list_sessions(
        self, user_key: str, chat_key: str | None = None
    ) -> list[dict[str, Any]]:
        where = ["user_key = ?"]
        params: list[Any] = [user_key]
        if chat_key:
            where.append("chat_key = ?")
            params.append(chat_key)
        return self._query_dicts(
            f"""
            SELECT s.session_id, s.user_key, s.chat_key, s.title, s.created_at, s.updated_at,
                   COALESCE(sa.agent_id, 'agent:default') AS agent_id
            FROM cp_sessions s
            LEFT JOIN cp_session_agents sa ON sa.session_id = s.session_id
            WHERE {" AND ".join(where)}
            ORDER BY s.updated_at DESC
            """,
            params,
        )

    def set_session_title(self, session_id: str, title: str) -> None:
        now = _iso_now()
        with self._lock, self._record_store.transaction():
            self._ensure_session_locked(session_id, user_key=None, chat_key=None)
            self._execute_count(
                "UPDATE cp_sessions SET title = ?, updated_at = ? WHERE session_id = ?",
                (title.strip(), now, session_id),
            )

    def get_session_title(self, session_id: str) -> str | None:
        row = self._query_one(
            "SELECT title FROM cp_sessions WHERE session_id = ? LIMIT 1",
            (session_id,),
        )
        if row is None:
            return None
        title = row["title"]
        return str(title) if title is not None else None

    def list_session_bindings(self, limit: int = 1000) -> list[dict[str, Any]]:
        return self._query_dicts(
            """
            SELECT
                cb.chat_key,
                cb.session_id,
                s.user_key AS owner_user_key,
                s.chat_key AS session_chat_key
            FROM cp_chat_bindings cb
            LEFT JOIN cp_sessions s ON s.session_id = cb.session_id
            ORDER BY cb.updated_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        )

    def set_agent(self, session_id: str, agent_id: str) -> None:
        if agent_id not in self._agents:
            raise ValueError(f"unknown agent id: {agent_id}")
        with self._lock, self._record_store.transaction():
            self._ensure_session_locked(session_id, user_key=None, chat_key=None)
            self._set_session_agent_locked(session_id, agent_id)

    def resolve_agent(self, session_id: str) -> str:
        row = self._query_one(
            "SELECT agent_id FROM cp_session_agents WHERE session_id = ? LIMIT 1",
            (session_id,),
        )
        if row is None:
            return "agent:default"
        return str(row["agent_id"] or "agent:default")

    def list_agents(self) -> list[dict[str, Any]]:
        return list(self._agents.values())

    def ensure_agent(self, agent_id: str, name: str | None = None) -> None:
        self._agents.setdefault(agent_id, {"id": agent_id, "name": name or agent_id})

    def _ensure_session_locked(
        self, session_id: str, user_key: str | None, chat_key: str | None
    ) -> None:
        now = _iso_now()
        self._execute_count(
            """
            INSERT INTO cp_sessions(session_id, user_key, chat_key, title, created_at, updated_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET
                user_key = COALESCE(cp_sessions.user_key, excluded.user_key),
                chat_key = COALESCE(cp_sessions.chat_key, excluded.chat_key),
                updated_at = excluded.updated_at
            """,
            (session_id, user_key, chat_key, None, now, now),
        )

    def _set_session_agent_locked(self, session_id: str, agent_id: str) -> None:
        now = _iso_now()
        self._execute_count(
            """
            INSERT INTO cp_session_agents(session_id, agent_id, updated_at)
            VALUES (?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET
                agent_id = excluded.agent_id,
                updated_at = excluded.updated_at
            """,
            (session_id, agent_id, now),
        )

    def get_user(self, user_key: str) -> dict[str, Any] | None:
        row = self._query_one(
            """
            SELECT user_key, role, display_name, profile_meta_json, created_at, updated_at
            FROM cp_users
            WHERE user_key = ?
            LIMIT 1
            """,
            (user_key,),
        )
        if row is None:
            return None
        result = dict(row)
        result["profile_meta"] = _json_load(result.pop("profile_meta_json", None))
        return result

    def upsert_user(
        self,
        user_key: str,
        role: str = "user",
        profile_meta: dict[str, Any] | None = None,
    ) -> None:
        now = _iso_now()
        self._execute_count(
            """
            INSERT INTO cp_users(user_key, role, profile_meta_json, created_at, updated_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(user_key) DO UPDATE SET
                role=excluded.role,
                profile_meta_json=excluded.profile_meta_json,
                updated_at=excluded.updated_at
            """,
            (user_key, role, _json_dump(profile_meta), now, now),
        )

    def put_inbound(
        self,
        chat_key: str,
        user_key: str,
        text: str | None,
        payload: dict[str, Any],
        session_id: str | None = None,
        agent_id: str | None = None,
        timestamp: str | None = None,
    ) -> int:
        now = timestamp or _iso_now()
        msg_id = payload.get("message_id") or uuid.uuid4().hex
        with self._lock, self._record_store.transaction():
            rows = self._query_dicts(
                """
                INSERT INTO cp_inbound_messages
                    (message_id, chat_key, user_key, session_id, agent_id, timestamp, text, payload_json)
                VALUES (?,?,?,?,?,?,?,?)
                RETURNING id
                """,
                (
                    msg_id,
                    chat_key,
                    user_key,
                    session_id,
                    agent_id,
                    now,
                    text,
                    _json_dump(payload),
                ),
            )
        return int(rows[0]["id"]) if rows else 0

    def put_outbound(
        self,
        chat_key: str,
        text: str | None,
        payload: dict[str, Any],
        session_id: str | None = None,
        agent_id: str | None = None,
        timestamp: str | None = None,
    ) -> int:
        now = timestamp or _iso_now()
        with self._lock, self._record_store.transaction():
            rows = self._query_dicts(
                """
                INSERT INTO cp_outbound_messages
                    (chat_key, session_id, agent_id, timestamp, text, payload_json)
                VALUES (?,?,?,?,?,?)
                RETURNING id
                """,
                (chat_key, session_id, agent_id, now, text, _json_dump(payload)),
            )
        return int(rows[0]["id"]) if rows else 0

    def attachment_refs_from_inputs(
        self, inputs: list[AttachmentInput | AttachmentRef]
    ) -> list[str]:
        refs: list[str] = []
        for item in inputs:
            if isinstance(item, AttachmentRef):
                refs.append(item.ref)
                continue
            refs.append(item.url or f"artifact://local/{uuid.uuid4().hex}")
        return refs

    def persist_inbound(self, inbound: InboundMessage, session_id: str) -> None:
        self.put_inbound(
            chat_key=inbound.chat_key,
            user_key=inbound.user_key,
            text=inbound.text,
            session_id=session_id,
            payload={
                "channel": inbound.channel,
                "thread_key": inbound.thread_key,
                "meta": inbound.meta,
                "metadata": inbound.metadata,
                "channel_message_id": inbound.channel_message_id,
            },
        )

    def append_turn(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        attachments: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        row = self._query_one(
            "SELECT user_key, chat_key FROM cp_sessions WHERE session_id = ? LIMIT 1",
            (session_id,),
        )
        user_key = str(row["user_key"]) if row and row["user_key"] else "user:unknown"
        chat_key = (
            str(row["chat_key"]) if row and row["chat_key"] else f"session:{session_id}"
        )
        with self._lock, self._record_store.transaction():
            self._ensure_session_locked(
                session_id, user_key=user_key, chat_key=chat_key
            )

        payload = {
            "role": role,
            "content": content,
            "attachments": attachments or [],
            "meta": meta or {},
            "source": "append_turn",
        }
        self.put_inbound(
            chat_key=chat_key,
            user_key=user_key,
            text=content,
            payload=payload,
            session_id=session_id,
            agent_id=None,
        )
        return session_id

    def list_turns(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._query_dicts(
            """
            SELECT id, message_id, chat_key, user_key, session_id, agent_id, timestamp, text, payload_json
            FROM cp_inbound_messages
            WHERE session_id = ?
            ORDER BY timestamp ASC, id ASC
            """,
            (session_id,),
        )
        turns: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = _json_load(item.pop("payload_json", None))
            turns.append(item)
        return turns

    def set_pending_clarify(self, session_id: str, payload: dict[str, Any]) -> None:
        now = _iso_now()
        self._execute_count(
            """
            INSERT INTO cp_pending_clarify(session_id, payload_json, updated_at)
            VALUES (?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (session_id, _json_dump(payload), now),
        )

    def get_pending_clarify(self, session_id: str) -> dict[str, Any] | None:
        row = self._query_one(
            """
            SELECT payload_json
            FROM cp_pending_clarify
            WHERE session_id = ?
            LIMIT 1
            """,
            (session_id,),
        )
        if row is None:
            return None
        return _json_load(row["payload_json"])

    def clear_pending_clarify(self, session_id: str) -> None:
        self._execute_count(
            "DELETE FROM cp_pending_clarify WHERE session_id = ?",
            (session_id,),
        )

    def list_pending_clarifies(self) -> list[dict[str, Any]]:
        rows = self._query_dicts(
            """
            SELECT session_id, payload_json
            FROM cp_pending_clarify
            ORDER BY updated_at ASC
            """
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = _json_load(row.get("payload_json"))
            if not isinstance(payload, dict):
                continue
            entry = dict(payload)
            entry.setdefault("session_id", str(row.get("session_id") or ""))
            result.append(entry)
        return result

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
        return self._inbox_outbox.enqueue_inbox(
            channel=channel,
            chat_id=chat_id,
            channel_message_id=channel_message_id,
            user_id=user_id,
            payload=payload,
            thread_id=thread_id,
            inbound_id=inbound_id,
        )

    def claim_inbox(
        self, *, lock_owner: str, reclaim_ttl_s: int = 120
    ) -> dict[str, Any] | None:
        return self._inbox_outbox.claim_inbox(
            lock_owner=lock_owner, reclaim_ttl_s=reclaim_ttl_s
        )

    def ack_inbox(self, inbox_id: str) -> None:
        self._inbox_outbox.ack_inbox(inbox_id)

    def fail_inbox(self, inbox_id: str, error: str) -> None:
        self._inbox_outbox.fail_inbox(inbox_id, error)

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
        return self._inbox_outbox.enqueue_outbox(
            channel=channel,
            chat_id=chat_id,
            payload=payload,
            thread_id=thread_id,
            reply_to=reply_to,
            outbox_id=outbox_id,
        )

    def claim_outbox(
        self, *, lock_owner: str, reclaim_ttl_s: int = 120
    ) -> dict[str, Any] | None:
        return self._inbox_outbox.claim_outbox(
            lock_owner=lock_owner, reclaim_ttl_s=reclaim_ttl_s
        )

    def mark_outbox_sent(self, outbox_id: str) -> None:
        self._inbox_outbox.mark_outbox_sent(outbox_id)

    def mark_outbox_retry(
        self,
        outbox_id: str,
        *,
        error: str,
        max_attempts: int = 8,
        max_backoff_s: int = 300,
    ) -> str:
        return self._inbox_outbox.mark_outbox_retry(
            outbox_id,
            error=error,
            max_attempts=max_attempts,
            max_backoff_s=max_backoff_s,
        )

    def get_outbox(self, outbox_id: str) -> dict[str, Any] | None:
        return self._inbox_outbox.get_outbox(outbox_id)

    def upsert_pairing(
        self,
        *,
        channel: str,
        chat_id: str,
        user_id: str,
        session_id: str,
        status: str = PRINCIPAL_BINDING_STATUS_ACTIVE,
        scopes: list[str] | tuple[str, ...] | None = None,
        note: str | None = None,
        pairing_id: str | None = None,
    ) -> str:
        return self._principals.upsert_pairing(
            channel=channel,
            chat_id=chat_id,
            user_id=user_id,
            session_id=session_id,
            status=status,
            scopes=scopes,
            note=note,
            pairing_id=pairing_id,
        )

    def get_pairing(self, *, channel: str, chat_id: str) -> dict[str, Any] | None:
        return self._principals.get_pairing(channel=channel, chat_id=chat_id)

    def list_pairings(
        self, *, channel: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return self._principals.list_pairings(channel=channel, limit=limit)

    def touch_pairing(self, *, channel: str, chat_id: str) -> None:
        self._principals.touch_pairing(channel=channel, chat_id=chat_id)

    def backfill_pairings_to_principals(
        self,
        *,
        channel: str | None = None,
        status: str = PRINCIPAL_BINDING_STATUS_ACTIVE,
        limit: int | None = None,
    ) -> dict[str, int]:
        return self._principals.backfill_pairings_to_principals(
            channel=channel,
            status=status,
            limit=limit,
        )

    def upsert_principal(
        self,
        *,
        principal_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        return self._principals.upsert_principal(principal_id=principal_id, meta=meta)

    def bind_principal_subject(
        self,
        *,
        principal_id: str,
        channel: str,
        subject_id: str,
        status: str = PRINCIPAL_BINDING_STATUS_ACTIVE,
        scopes: list[str] | tuple[str, ...] | None = None,
        note: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self._principals.bind_principal_subject(
            principal_id=principal_id,
            channel=channel,
            subject_id=subject_id,
            status=status,
            scopes=scopes,
            note=note,
            meta=meta,
        )

    def resolve_principal(self, *, channel: str, subject_id: str) -> str | None:
        return self._principals.resolve_principal(
            channel=channel, subject_id=subject_id
        )

    def get_channel_subject(
        self, *, channel: str, subject_id: str
    ) -> dict[str, Any] | None:
        return self._principals.get_channel_subject(
            channel=channel, subject_id=subject_id
        )

    def touch_channel_subject(self, *, channel: str, subject_id: str) -> None:
        self._principals.touch_channel_subject(channel=channel, subject_id=subject_id)

    def increment_rate_limit(
        self,
        *,
        key_type: str,
        key_id: str,
        window_seconds: int,
        limit: int,
    ) -> dict[str, Any]:
        now = int(datetime.now(timezone.utc).timestamp())
        seconds = max(1, int(window_seconds))
        window_start = now - (now % seconds)
        with self._lock, self._record_store.transaction():
            self._execute_count(
                """
                INSERT INTO cp_rate_limits(key_type, key_id, window_start, count, updated_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(key_type, key_id, window_start) DO UPDATE SET
                    count = cp_rate_limits.count + 1,
                    updated_at = excluded.updated_at
                """,
                (key_type, key_id, window_start, 1, _iso_now()),
            )
            row = self._query_one(
                """
                SELECT count FROM cp_rate_limits
                WHERE key_type = ? AND key_id = ? AND window_start = ?
                LIMIT 1
                """,
                (key_type, key_id, window_start),
            )
            count = int(row["count"] if row is not None else 0)
            self._execute_count(
                """
                DELETE FROM cp_rate_limits
                WHERE key_type = ? AND key_id = ? AND window_start < ?
                """,
                (key_type, key_id, window_start - (seconds * 3)),
            )
        return {
            "allowed": count <= int(limit),
            "count": count,
            "limit": int(limit),
            "window_start": window_start,
            "window_seconds": seconds,
        }

    def put_audit(self, event: Any) -> None:
        if hasattr(event, "to_dict"):
            d = event.to_dict()
        else:
            d = dict(event)
        self._execute_count(
            """
            INSERT INTO cp_audit_events
                (event_id, timestamp, event_type, severity, outcome,
                 chat_key, user_key, session_id, agent_id,
                 trace_id, span_id, details_json, error_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(event_id) DO NOTHING
            """,
            (
                d.get("event_id") or uuid.uuid4().hex,
                d.get("timestamp") or _iso_now(),
                d.get("event_type", "unknown"),
                d.get("severity", "info"),
                d.get("outcome", "ok"),
                d.get("chat_key"),
                d.get("user_key"),
                d.get("session_id"),
                d.get("agent_id"),
                d.get("trace_id") or uuid.uuid4().hex,
                d.get("span_id"),
                _json_dump(d.get("details")),
                _json_dump(d.get("error")) if d.get("error") else None,
            ),
        )

    def list_audit(
        self,
        *,
        event_type: str | None = None,
        session_id: str | None = None,
        trace_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        where = ["1=1"]
        params: list[Any] = []
        if event_type:
            where.append("event_type = ?")
            params.append(event_type)
        if session_id:
            where.append("session_id = ?")
            params.append(session_id)
        if trace_id:
            where.append("trace_id = ?")
            params.append(trace_id)
        params.append(max(1, min(int(limit), 5000)))
        return self._query_dicts(
            f"SELECT * FROM cp_audit_events WHERE {' AND '.join(where)} ORDER BY timestamp ASC LIMIT ?",
            params,
        )


class SQLiteControlPlaneStore(BaseModuleSQLiteStore, _ControlPlaneStoreMixin):
    def __init__(
        self,
        sqlite_path: str | Path,
        *,
        record_store: RecordStore | None = None,
        wal: bool = True,
    ) -> None:
        super().__init__(sqlite_path, wal=wal, record_store=record_store)
        self._post_store_init()

    def _init_schema(self) -> None:
        self._apply_migrations()

    def _list_migrations(self) -> list[str]:
        return _list_migrations()

    def _module_package(self) -> str:
        return __package__


class PostgresControlPlaneStore(BaseModuleStore, _ControlPlaneStoreMixin):
    def __init__(self, *, record_store: RecordStore) -> None:
        super().__init__(record_store=record_store)
        self._post_store_init()

    def _ddl_for_backend(self, statement: str) -> str:
        return _postgresify_ddl(statement)

    def _init_schema(self) -> None:
        self._apply_migrations()

    def _list_migrations(self) -> list[str]:
        return _list_migrations()

    def _module_package(self) -> str:
        return __package__


__all__ = ["PostgresControlPlaneStore", "SQLiteControlPlaneStore"]
