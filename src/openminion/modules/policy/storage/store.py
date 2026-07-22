from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional
from collections.abc import Mapping
from uuid import uuid4

from openminion.modules.storage.runtime.module_store import (
    BaseModuleSQLiteStore,
    BaseModuleStore,
)
from openminion.modules.storage.record_store import RecordStore
from .base import PolicyStore
from .migrations import list_migrations
from ..constants import POLICY_DURATION_ONCE
from ..models import PolicyGrant, PolicyGrantInput, utc_now_iso


def _to_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _parse_json(raw: str | None, fallback: Any) -> Any:
    if raw in {None, ""}:
        return fallback
    try:
        return json.loads(str(raw))
    except json.JSONDecodeError:
        return fallback


def _create_policy_schema(record_store: RecordStore) -> None:
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS policy_grants (
            grant_id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            effect TEXT NOT NULL,
            tool TEXT NOT NULL,
            method TEXT NOT NULL,
            target_json TEXT NOT NULL DEFAULT '{}',
            risk_floor TEXT,
            duration_type TEXT NOT NULL,
            expires_at TEXT,
            session_id TEXT,
            invocation_hash TEXT,
            max_uses INTEGER,
            uses_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            revoked_at TEXT,
            reason TEXT,
            created_trace_id TEXT
        )
        """
    )
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS policy_decisions (
            decision_id TEXT PRIMARY KEY,
            trace_id TEXT,
            session_id TEXT,
            agent_id TEXT,
            invocation_id TEXT,
            tool TEXT,
            method TEXT,
            decision TEXT NOT NULL,
            matched_grant_id TEXT,
            reason_code TEXT,
            risk_spec_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS policy_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    record_store.execute_count(
        """
        CREATE INDEX IF NOT EXISTS idx_policy_grants_subject
            ON policy_grants(subject_id, tool, method)
        """
    )
    record_store.execute_count(
        """
        CREATE INDEX IF NOT EXISTS idx_policy_grants_active
            ON policy_grants(subject_id, revoked_at, expires_at)
        """
    )
    record_store.execute_count(
        """
        CREATE INDEX IF NOT EXISTS idx_policy_grants_invocation
            ON policy_grants(invocation_hash)
        """
    )
    record_store.execute_count(
        """
        CREATE INDEX IF NOT EXISTS idx_policy_decisions_trace
            ON policy_decisions(trace_id, created_at)
        """
    )
    record_store.execute_count(
        """
        CREATE INDEX IF NOT EXISTS idx_policy_decisions_session
            ON policy_decisions(session_id, created_at)
        """
    )


class _PolicyStoreMixin(PolicyStore):
    """Backend-neutral policy store behavior shared by SQLite and Postgres."""

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__

    def _init_schema(self) -> None:
        with self._lock:
            _create_policy_schema(self._record_store)

    def close(self) -> None:
        BaseModuleStore.close(self)

    def create_grant(self, grant: PolicyGrantInput) -> str:
        now = utc_now_iso()
        grant_id = str(uuid4())
        self._record_store.execute_count(
            """
            INSERT INTO policy_grants (
                grant_id, subject_id, effect, tool, method, target_json, risk_floor,
                duration_type, expires_at, session_id, invocation_hash, max_uses, uses_count,
                created_at, updated_at, revoked_at, reason, created_trace_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, NULL, ?, ?)
            """,
            (
                grant_id,
                grant.subject_id,
                grant.effect,
                grant.tool,
                grant.method,
                _to_json(grant.target_json),
                grant.risk_floor,
                grant.duration_type,
                grant.expires_at,
                grant.session_id,
                grant.invocation_hash,
                grant.max_uses,
                now,
                now,
                grant.reason,
                grant.created_trace_id,
            ),
        )
        return grant_id

    def revoke_grant(self, grant_id: str) -> bool:
        now = utc_now_iso()
        count = self._record_store.execute_count(
            """
            UPDATE policy_grants
            SET revoked_at = ?, updated_at = ?
            WHERE grant_id = ? AND revoked_at IS NULL
            """,
            (now, now, grant_id),
        )
        return count > 0

    def list_grants(
        self,
        *,
        subject_id: Optional[str] = None,
        effect: Optional[str] = None,
        tool: Optional[str] = None,
        method: Optional[str] = None,
        active_only: bool = False,
    ) -> list[PolicyGrant]:
        where = []
        params: list[Any] = []
        if subject_id:
            where.append("subject_id = ?")
            params.append(subject_id)
        if effect:
            where.append("effect = ?")
            params.append(effect)
        if tool:
            where.append("tool = ?")
            params.append(tool)
        if method:
            where.append("method = ?")
            params.append(method)
        if active_only:
            where.append("revoked_at IS NULL")
            where.append("(expires_at IS NULL OR expires_at > ?)")
            params.append(utc_now_iso())

        sql = "SELECT * FROM policy_grants"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC"
        rows = self._record_store.query_dicts(sql, tuple(params))
        return [self._row_to_grant(row) for row in rows]

    def get_grant(self, grant_id: str) -> Optional[PolicyGrant]:
        rows = self._record_store.query_dicts(
            "SELECT * FROM policy_grants WHERE grant_id = ?",
            (grant_id,),
        )
        if not rows:
            return None
        return self._row_to_grant(rows[0])

    def consume_grant_use(self, grant_id: str) -> Optional[PolicyGrant]:
        now = utc_now_iso()
        grant = self.get_grant(grant_id)
        if grant is None:
            return None

        new_uses = grant.uses_count + 1
        revoke_at = grant.revoked_at
        if grant.duration_type == POLICY_DURATION_ONCE:
            revoke_at = now
        if grant.max_uses is not None and new_uses >= grant.max_uses:
            revoke_at = now

        self._record_store.execute_count(
            """
            UPDATE policy_grants
            SET uses_count = ?, updated_at = ?, revoked_at = COALESCE(?, revoked_at)
            WHERE grant_id = ?
            """,
            (new_uses, now, revoke_at, grant_id),
        )
        return self.get_grant(grant_id)

    def cleanup_expired(self) -> int:
        now = utc_now_iso()
        return self._record_store.execute_count(
            """
            UPDATE policy_grants
            SET revoked_at = ?, updated_at = ?
            WHERE revoked_at IS NULL AND expires_at IS NOT NULL AND expires_at <= ?
            """,
            (now, now, now),
        )

    def log_decision(
        self,
        *,
        trace_id: Optional[str],
        session_id: Optional[str],
        agent_id: Optional[str],
        invocation_id: str,
        tool: str,
        method: str,
        decision: str,
        matched_grant_id: Optional[str],
        reason_code: str,
        risk_spec: dict[str, Any],
    ) -> str:
        now = utc_now_iso()
        decision_id = str(uuid4())
        self._record_store.execute_count(
            """
            INSERT INTO policy_decisions (
                decision_id, trace_id, session_id, agent_id, invocation_id, tool, method,
                decision, matched_grant_id, reason_code, risk_spec_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                trace_id,
                session_id,
                agent_id,
                invocation_id,
                tool,
                method,
                decision,
                matched_grant_id,
                reason_code,
                _to_json(risk_spec),
                now,
            ),
        )
        return decision_id

    def list_decisions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._record_store.query_dicts(
            """
            SELECT *
            FROM policy_decisions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        )
        return [
            {
                **dict(row),
                "risk_spec_json": _parse_json(row.get("risk_spec_json"), {}),
            }
            for row in rows
        ]

    def set_setting(self, key: str, value: str) -> None:
        now = utc_now_iso()
        self._record_store.execute_count(
            """
            INSERT INTO policy_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now),
        )

    def get_setting(self, key: str) -> Optional[str]:
        rows = self._record_store.query_dicts(
            "SELECT value FROM policy_settings WHERE key = ?",
            (key,),
        )
        if not rows:
            return None
        return str(rows[0]["value"])

    def _row_to_grant(self, row: Mapping[str, Any]) -> PolicyGrant:
        payload = dict(row)
        payload["target_json"] = _parse_json(payload.get("target_json"), {})
        return PolicyGrant(**payload)


class SQLitePolicyStore(_PolicyStoreMixin, BaseModuleSQLiteStore):
    """SQLite-backed policy store (module-owned schema + SQL)."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        record_store: RecordStore | None = None,
        wal: bool = True,
    ) -> None:
        BaseModuleSQLiteStore.__init__(
            self,
            database_path,
            wal=wal,
            record_store=record_store,
        )

    @property
    def path(self) -> Path:
        return self.sqlite_path


class PostgresPolicyStore(_PolicyStoreMixin, BaseModuleStore):
    """Postgres-backed policy store."""

    def __init__(self, *, record_store: RecordStore) -> None:
        BaseModuleStore.__init__(self, record_store=record_store)


__all__ = ("PostgresPolicyStore", "SQLitePolicyStore")
