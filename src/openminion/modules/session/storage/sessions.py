from typing import Any
from collections.abc import Callable, Mapping
from uuid import uuid4

from openminion.modules.storage.record_store import RecordStore

from .json_utils import to_json
from .rows import row_to_session


class SessionLifecycleHelper:
    def __init__(
        self,
        *,
        record_store: RecordStore,
        lock: Any,
        invalidate_slice_cache: Callable[[str], None],
        append_event: Callable[..., str],
        utc_now_iso: Callable[[], str],
    ) -> None:
        self._record_store = record_store
        self._lock = lock
        self._invalidate_slice_cache = invalidate_slice_cache
        self._append_event = append_event
        self._utc_now_iso = utc_now_iso

    def _first_row(
        self,
        sql: str,
        params: tuple[object, ...],
    ) -> dict[str, Any] | None:
        rows = self._record_store.query_dicts(sql, params)
        return rows[0] if rows else None

    def create_session(
        self,
        *,
        initial_agent_id: str | None = None,
        profile_version: str | None = None,
        title: str | None = None,
        tags: list[str] | None = None,
        status: str = "active",
        session_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        sid = (session_id or "").strip() or uuid4().hex
        now = self._utc_now_iso()
        with self._lock:
            self._record_store.execute_count(
                """
                INSERT INTO sessions(
                  session_id, created_at, updated_at, title, status, active_agent_id,
                  active_profile_version, participants_json, root_goal, tags_json,
                  config_snapshot_ref, meta_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sid,
                    now,
                    now,
                    title,
                    status,
                    initial_agent_id,
                    profile_version,
                    "[]",
                    None,
                    to_json(tags or []),
                    None,
                    to_json(meta or {}),
                ),
            )
            self._invalidate_slice_cache(sid)

        if initial_agent_id is not None or profile_version is not None:
            self._append_event(
                sid,
                event_type="agent.bound",
                payload={
                    "agent_id": initial_agent_id,
                    "profile_version": profile_version,
                },
                actor_type="system",
                actor_id=None,
                importance=1,
            )
        return sid

    def list_sessions(
        self,
        *,
        filters: Mapping[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        filter_map = dict(filters or {})

        if filter_map.get("status"):
            clauses.append("status = ?")
            params.append(str(filter_map["status"]))
        if filter_map.get("active_agent_id"):
            clauses.append("active_agent_id = ?")
            params.append(str(filter_map["active_agent_id"]))

        query = """
            SELECT session_id, created_at, updated_at, title, status, active_agent_id,
                   active_profile_version, participants_json, root_goal, tags_json,
                   config_snapshot_ref, meta_json
            FROM sessions
        """
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC, session_id DESC LIMIT ?"
        params.append(max(1, int(limit)))

        with self._lock:
            rows = self._record_store.query_dicts(query, tuple(params))
        return [row_to_session(row) for row in rows]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._first_row(
                """
                SELECT session_id, created_at, updated_at, title, status, active_agent_id,
                       active_profile_version, participants_json, root_goal, tags_json,
                       config_snapshot_ref, meta_json
                FROM sessions
                WHERE session_id = ?
                """,
                (session_id,),
            )
        if row is None:
            return None
        return row_to_session(row)

    def set_status(self, session_id: str, status: str) -> None:
        self.update_session(session_id, {"status": status})

    def update_session_status(self, session_id: str, status: str) -> None:
        self.set_status(session_id, status)

    def bind_agent(
        self,
        session_id: str,
        agent_id: str,
        profile_version: str,
        *,
        render_version: str | None = None,
        reason: str | None = None,
    ) -> None:
        with self._lock:
            current = self.get_session(session_id)
            if current is None:
                raise ValueError(f"session not found: {session_id}")
            now = self._utc_now_iso()
            from_agent_id = current.get("active_agent_id")
            self._record_store.execute_count(
                """
                UPDATE sessions
                SET active_agent_id = ?, active_profile_version = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (agent_id, profile_version, now, session_id),
            )
            self._invalidate_slice_cache(session_id)

        if from_agent_id and from_agent_id != agent_id:
            self._append_event(
                session_id,
                event_type="agent.switched",
                payload={
                    "from_agent_id": from_agent_id,
                    "to_agent_id": agent_id,
                    "reason": reason,
                },
                actor_type="system",
                importance=1,
            )

        payload: dict[str, Any] = {
            "agent_id": agent_id,
            "profile_version": profile_version,
        }
        if render_version is not None:
            payload["render_version"] = render_version
        if reason is not None:
            payload["reason"] = reason
        self._append_event(
            session_id,
            event_type="agent.bound",
            payload=payload,
            actor_type="system",
            importance=1,
        )

    def update_session(self, session_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = self.get_session(session_id)
            if current is None:
                raise ValueError(f"session not found: {session_id}")
            updated = dict(current)
            updated.update(patch)
            now = self._utc_now_iso()
            self._record_store.execute_count(
                """
                UPDATE sessions
                SET updated_at = ?, title = ?, status = ?, active_agent_id = ?,
                    active_profile_version = ?, participants_json = ?, root_goal = ?,
                    tags_json = ?, config_snapshot_ref = ?, meta_json = ?
                WHERE session_id = ?
                """,
                (
                    now,
                    updated.get("title"),
                    updated.get("status", "active"),
                    updated.get("active_agent_id"),
                    updated.get("active_profile_version"),
                    to_json(updated.get("participants", [])),
                    updated.get("root_goal"),
                    to_json(updated.get("tags", [])),
                    updated.get("config_snapshot_ref"),
                    to_json(updated.get("meta", {})),
                    session_id,
                ),
            )
            self._invalidate_slice_cache(session_id)
            refreshed = self.get_session(session_id)
        if refreshed is None:
            raise RuntimeError("failed to reload session after update")
        return refreshed

    def archive_session(self, session_id: str) -> None:
        self.update_session(session_id, {"status": "archived"})
