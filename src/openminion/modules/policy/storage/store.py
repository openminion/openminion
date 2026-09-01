from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from openminion.modules.storage.runtime.module_store import (
    BaseModuleSQLiteStore,
    BaseModuleStore,
)
from openminion.modules.storage.record_store import RecordStore
from .base import PolicyStore
from .migrations import list_migrations
from ..constants import POLICY_DURATION_ONCE
from ..models import (
    PendingPolicyConfirmation,
    PolicyControlError,
    PolicyGrant,
    PolicyGrantInput,
    utc_now_iso,
)


def _to_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _parse_json(raw: str | None, fallback: Any) -> Any:
    if raw in {None, ""}:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _mapping_contains(value: Mapping[str, Any], required: Mapping[str, Any]) -> bool:
    for key, expected in required.items():
        actual = value.get(key)
        if isinstance(expected, Mapping):
            if not isinstance(actual, Mapping) or not _mapping_contains(
                actual, expected
            ):
                return False
        elif actual != expected:
            return False
    return True


def _create_blockchain_policy_schema(record_store: RecordStore) -> None:
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS policy_pending_confirmations (
            approval_id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            tool TEXT NOT NULL,
            method TEXT NOT NULL,
            invocation_hash TEXT NOT NULL,
            invocation_id TEXT NOT NULL,
            trace_id TEXT,
            session_id TEXT,
            preview_json TEXT NOT NULL,
            state TEXT NOT NULL,
            resolution_action TEXT,
            grant_id TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            resolved_at TEXT
        )
        """
    )
    record_store.execute_count(
        """
        CREATE INDEX IF NOT EXISTS idx_policy_pending_confirmation_lookup
            ON policy_pending_confirmations(
                subject_id, tool, method, invocation_hash, state
            )
        """
    )


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
            created_trace_id TEXT,
            approval_id TEXT
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
            approval_id TEXT,
            invocation_hash TEXT,
            risk_spec_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    _create_blockchain_policy_schema(record_store)
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
    _lock: Any
    _record_store: RecordStore

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
        self._insert_grant(grant_id=grant_id, grant=grant, now=now)
        return grant_id

    def _insert_grant(
        self,
        *,
        grant_id: str,
        grant: PolicyGrantInput,
        now: str,
    ) -> None:
        self._record_store.execute_count(
            """
            INSERT INTO policy_grants (
                grant_id, subject_id, effect, tool, method, target_json, risk_floor,
                duration_type, expires_at, session_id, invocation_hash, max_uses, uses_count,
                created_at, updated_at, revoked_at, reason, created_trace_id,
                approval_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, NULL, ?, ?, ?)
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
                grant.approval_id,
            ),
        )

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
        return self._row_to_grant(rows[0]) if rows else None

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

    def resolve_active_grant_for_use(
        self,
        grant_id: str,
        *,
        subject_id: str,
        tool: str,
        method: str,
        required_target: Mapping[str, Any] | None = None,
    ) -> Optional[PolicyGrant]:
        """Resolve and consume one exact active grant under the policy lock."""

        now = utc_now_iso()
        with self._lock:
            rows = self._record_store.query_dicts(
                """
                SELECT * FROM policy_grants
                WHERE grant_id = ?
                  AND subject_id = ?
                  AND effect = 'allow'
                  AND tool = ?
                  AND method = ?
                  AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?)
                  AND (max_uses IS NULL OR uses_count < max_uses)
                """,
                (grant_id, subject_id, tool, method, now),
            )
            if not rows:
                return None
            grant = self._row_to_grant(rows[0])
            if required_target is not None and not _mapping_contains(
                grant.target_json, required_target
            ):
                return None
            revoke_at = (
                now
                if grant.duration_type == POLICY_DURATION_ONCE
                or (
                    grant.max_uses is not None
                    and grant.uses_count + 1 >= grant.max_uses
                )
                else None
            )
            updated = self._record_store.execute_count(
                """
                UPDATE policy_grants
                SET uses_count = uses_count + 1,
                    updated_at = ?,
                    revoked_at = COALESCE(?, revoked_at)
                WHERE grant_id = ?
                  AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?)
                  AND (max_uses IS NULL OR uses_count < max_uses)
                """,
                (now, revoke_at, grant_id, now),
            )
            return grant if updated == 1 else None

    def get_or_create_pending_confirmation(
        self,
        *,
        subject_id: str,
        tool: str,
        method: str,
        invocation_hash: str,
        invocation_id: str,
        trace_id: str | None,
        session_id: str | None,
        preview: Mapping[str, Any],
        ttl_seconds: int,
    ) -> PendingPolicyConfirmation:
        now = utc_now_iso()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        ).isoformat()
        with self._lock, self._record_store.transaction():
            rows = self._record_store.query_dicts(
                """
                SELECT * FROM policy_pending_confirmations
                WHERE subject_id = ? AND tool = ? AND method = ?
                  AND invocation_hash = ? AND state = 'pending'
                  AND expires_at > ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (subject_id, tool, method, invocation_hash, now),
            )
            if rows:
                return self._row_to_pending_confirmation(rows[0])
            approval_id = str(uuid4())
            self._record_store.execute_count(
                """
                INSERT INTO policy_pending_confirmations (
                    approval_id, subject_id, tool, method, invocation_hash,
                    invocation_id, trace_id, session_id, preview_json, state,
                    resolution_action, grant_id, created_at, expires_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?, NULL)
                """,
                (
                    approval_id,
                    subject_id,
                    tool,
                    method,
                    invocation_hash,
                    invocation_id,
                    trace_id,
                    session_id,
                    _to_json(preview),
                    now,
                    expires_at,
                ),
            )
            return self._row_to_pending_confirmation(
                self._record_store.query_dicts(
                    "SELECT * FROM policy_pending_confirmations WHERE approval_id = ?",
                    (approval_id,),
                )[0]
            )

    def resolve_confirmation(self, approval_id: str, action: str) -> str | None:
        now = utc_now_iso()
        with self._lock, self._record_store.transaction():
            rows = self._record_store.query_dicts(
                "SELECT * FROM policy_pending_confirmations WHERE approval_id = ?",
                (approval_id,),
            )
            if not rows:
                raise PolicyControlError(
                    "PENDING_CONFIRMATION_NOT_FOUND",
                    "Pending confirmation was not found.",
                )
            pending = self._row_to_pending_confirmation(rows[0])
            if pending.state != "pending":
                if pending.resolution_action == action:
                    return pending.grant_id
                raise PolicyControlError(
                    "PENDING_CONFIRMATION_ALREADY_RESOLVED",
                    "Pending confirmation was already resolved.",
                )
            if pending.expires_at <= now:
                self._record_store.execute_count(
                    """
                    UPDATE policy_pending_confirmations
                    SET state = 'expired', resolved_at = ?
                    WHERE approval_id = ? AND state = 'pending'
                    """,
                    (now, approval_id),
                )
                raise PolicyControlError(
                    "PENDING_CONFIRMATION_EXPIRED",
                    "Pending confirmation expired.",
                )

            grant_id: str | None = None
            state = "denied"
            if action == "allow_once":
                grant_id = str(uuid4())
                self._insert_grant(
                    grant_id=grant_id,
                    grant=PolicyGrantInput(
                        effect="allow",
                        subject_id=pending.subject_id,
                        tool=pending.tool,
                        method=pending.method,
                        duration_type="once",
                        invocation_hash=pending.invocation_hash,
                        max_uses=1,
                        reason="created_from_pending_confirmation",
                        created_trace_id=pending.trace_id,
                        approval_id=pending.approval_id,
                    ),
                    now=now,
                )
                state = "allowed"
            elif action != "deny":
                raise ValueError("confirmation action must be allow_once|deny")

            self._record_store.execute_count(
                """
                UPDATE policy_pending_confirmations
                SET state = ?, resolution_action = ?, grant_id = ?, resolved_at = ?
                WHERE approval_id = ? AND state = 'pending'
                """,
                (state, action, grant_id, now, approval_id),
            )
            return grant_id

    def resolve_matching_active_grant_for_use(
        self,
        *,
        subject_id: str,
        tool: str,
        method: str,
        invocation_hash: str,
    ) -> PolicyGrant | None:
        now = utc_now_iso()
        with self._lock, self._record_store.transaction():
            rows = self._record_store.query_dicts(
                """
                SELECT * FROM policy_grants
                WHERE subject_id = ? AND effect = 'allow'
                  AND tool = ? AND method = ? AND invocation_hash = ?
                  AND duration_type = 'once' AND approval_id IS NOT NULL
                  AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?)
                  AND (max_uses IS NULL OR uses_count < max_uses)
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (subject_id, tool, method, invocation_hash, now),
            )
            if not rows:
                return None
            grant = self._row_to_grant(rows[0])
            updated = self._record_store.execute_count(
                """
                UPDATE policy_grants
                SET uses_count = uses_count + 1, updated_at = ?, revoked_at = ?
                WHERE grant_id = ? AND revoked_at IS NULL
                  AND (max_uses IS NULL OR uses_count < max_uses)
                """,
                (now, now, grant.grant_id),
            )
            return grant if updated == 1 else None

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
        approval_id: Optional[str] = None,
        invocation_hash: Optional[str] = None,
        reason_code: str,
        risk_spec: dict[str, Any],
    ) -> str:
        now = utc_now_iso()
        decision_id = str(uuid4())
        self._record_store.execute_count(
            """
            INSERT INTO policy_decisions (
                decision_id, trace_id, session_id, agent_id, invocation_id, tool, method,
                decision, matched_grant_id, reason_code, approval_id,
                invocation_hash, risk_spec_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                approval_id,
                invocation_hash,
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
        return str(rows[0]["value"]) if rows else None

    def _row_to_grant(self, row: Mapping[str, Any]) -> PolicyGrant:
        payload = dict(row)
        payload["target_json"] = _parse_json(payload.get("target_json"), {})
        return PolicyGrant(**payload)

    def _row_to_pending_confirmation(
        self, row: Mapping[str, Any]
    ) -> PendingPolicyConfirmation:
        payload = dict(row)
        payload["preview"] = _parse_json(payload.pop("preview_json", None), {})
        return PendingPolicyConfirmation(**payload)


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
