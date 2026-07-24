"""Runtime memory-log retention enforcement."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from typing import Any, Literal

from sophiagraph.audit.events import MemoryAuditEvent

from openminion.base.time import utc_now
from openminion.modules.memory.errors import InvalidArgumentError
from openminion.modules.memory.runtime.purge import decode_evidence_ref_values
from openminion.modules.memory.runtime.gc_records import remove_collected_artifact_refs
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.telemetry.events.catalog import MEMORY_RETENTION_ENFORCE

RetentionStatus = Literal["dry_run", "enforced", "unsupported", "noop"]

_DELETE_REASON = "runtime_retention_enforced"


@dataclass(frozen=True)
class RuntimeMemoryRetentionPolicy:
    """Structured runtime retention settings for session memory logs."""

    log_retention_days: int = 30
    patch_retention_count: int = 200

    def __post_init__(self) -> None:
        if self.log_retention_days <= 0:
            raise InvalidArgumentError(
                "log_retention_days must be positive",
                details={"field": "log_retention_days", "value": self.log_retention_days},
            )
        if self.patch_retention_count <= 0:
            raise InvalidArgumentError(
                "patch_retention_count must be positive",
                details={
                    "field": "patch_retention_count",
                    "value": self.patch_retention_count,
                },
            )


@dataclass(frozen=True)
class RuntimeMemoryRetentionReport:
    """Content-free retention outcome for audit and tests."""

    status: RetentionStatus
    cutoff: str
    eligible_record_ids: tuple[str, ...] = ()
    retained_record_ids: tuple[str, ...] = ()
    deleted_record_ids: tuple[str, ...] = ()
    unsupported_reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def eligible_count(self) -> int:
        return len(self.eligible_record_ids)

    @property
    def deleted_count(self) -> int:
        return len(self.deleted_record_ids)


def dry_run_runtime_memory_retention(
    store: Any,
    policy: RuntimeMemoryRetentionPolicy,
    *,
    now: datetime | None = None,
) -> RuntimeMemoryRetentionReport:
    """Return eligible session-memory rows without mutating the store."""

    sqlite_store = _sqlite_store_or_none(store)
    cutoff = _cutoff(now=now, policy=policy)
    if sqlite_store is None:
        return _unsupported_report(store=store, cutoff=cutoff)
    with sqlite_store._connect() as conn:
        eligible, retained = _eligible_session_record_ids(
            conn,
            cutoff=cutoff,
            patch_retention_count=policy.patch_retention_count,
        )
    return RuntimeMemoryRetentionReport(
        status="dry_run",
        cutoff=cutoff.isoformat(),
        eligible_record_ids=tuple(eligible),
        retained_record_ids=tuple(retained),
        details=_policy_details(policy),
    )


def enforce_runtime_memory_retention(
    store: Any,
    policy: RuntimeMemoryRetentionPolicy,
    *,
    now: datetime | None = None,
    before_commit: Callable[[], None] | None = None,
) -> RuntimeMemoryRetentionReport:
    """Soft-delete eligible session-memory rows with transactional rollback."""

    sqlite_store = _sqlite_store_or_none(store)
    cutoff = _cutoff(now=now, policy=policy)
    if sqlite_store is None:
        report = _unsupported_report(store=store, cutoff=cutoff)
        _append_audit_event(store, report)
        return report

    removed_edges: list[tuple[str, list[Any]]] = []
    with sqlite_store._write_lock, sqlite_store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            eligible, retained = _eligible_session_record_ids(
                conn,
                cutoff=cutoff,
                patch_retention_count=policy.patch_retention_count,
            )
            if not eligible:
                conn.execute("COMMIT")
                report = RuntimeMemoryRetentionReport(
                    status="noop",
                    cutoff=cutoff.isoformat(),
                    retained_record_ids=tuple(retained),
                    details=_policy_details(policy),
                )
                _append_audit_event(store, report)
                return report
            removed_edges = _soft_delete_records(
                conn,
                record_ids=eligible,
                now_iso=_now_iso(now),
            )
            if before_commit is not None:
                before_commit()
            conn.execute("COMMIT")
        except (sqlite3.Error, RuntimeError, OSError, TypeError, ValueError):
            conn.execute("ROLLBACK")
            raise
    remove_collected_artifact_refs(sqlite_store, removed_edges)
    report = RuntimeMemoryRetentionReport(
        status="enforced",
        cutoff=cutoff.isoformat(),
        eligible_record_ids=tuple(eligible),
        retained_record_ids=tuple(retained),
        deleted_record_ids=tuple(eligible),
        details=_policy_details(policy),
    )
    _append_audit_event(store, report)
    return report


def _sqlite_store_or_none(store: Any) -> SQLiteMemoryStore | None:
    if isinstance(store, SQLiteMemoryStore):
        return store
    wrapped = getattr(store, "_store", None)
    if isinstance(wrapped, SQLiteMemoryStore):
        return wrapped
    return None


def _cutoff(
    *,
    now: datetime | None,
    policy: RuntimeMemoryRetentionPolicy,
) -> datetime:
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current - timedelta(days=max(1, int(policy.log_retention_days)))


def _now_iso(now: datetime | None) -> str:
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.isoformat()


def _eligible_session_record_ids(
    conn: Any,
    *,
    cutoff: datetime,
    patch_retention_count: int,
) -> tuple[list[str], list[str]]:
    rows = conn.execute(
        """
        SELECT id, scope, created_at, updated_at, meta_json
          FROM memory_records
         WHERE is_deleted = 0
           AND scope LIKE 'session:%'
         ORDER BY scope ASC, updated_at DESC, created_at DESC, id DESC
        """
    ).fetchall()
    by_scope: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        by_scope[str(row["scope"] or "")].append(row)

    eligible: dict[str, None] = {}
    retained: dict[str, None] = {}
    for scope_rows in by_scope.values():
        for index, row in enumerate(scope_rows):
            record_id = str(row["id"])
            if _has_structural_retention_hold(row["meta_json"]):
                retained.setdefault(record_id, None)
                continue
            created_at = _parse_datetime(str(row["created_at"] or ""))
            if created_at is not None and created_at < cutoff:
                eligible.setdefault(record_id, None)
                continue
            if index >= patch_retention_count:
                eligible.setdefault(record_id, None)
    return list(eligible), list(retained)


def _has_structural_retention_hold(raw_meta: str | None) -> bool:
    try:
        meta = json.loads(str(raw_meta or "{}"))
    except json.JSONDecodeError:
        return False
    if not isinstance(meta, dict):
        return False
    if meta.get("retention_hold") is True or meta.get("legal_hold") is True:
        return True
    privacy_policy = meta.get("privacy_policy")
    return (
        isinstance(privacy_policy, dict)
        and str(privacy_policy.get("decision_reason") or "") == "retention_hold"
    )


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _soft_delete_records(
    conn: Any,
    *,
    record_ids: list[str],
    now_iso: str,
) -> list[tuple[str, list[Any]]]:
    if not record_ids:
        return []
    conn.execute("CREATE TEMP TABLE memory_retention_ids (id TEXT PRIMARY KEY)")
    try:
        conn.executemany(
            "INSERT INTO memory_retention_ids(id) VALUES (?)",
            [(record_id,) for record_id in record_ids],
        )
        rows = conn.execute(
            "SELECT id, evidence_json FROM memory_records "
            "WHERE id IN (SELECT id FROM memory_retention_ids)"
        ).fetchall()
        removed_edges = [
            (str(row["id"]), decode_evidence_ref_values(row["evidence_json"]))
            for row in rows
        ]
        conn.execute(
            """
            UPDATE memory_records
               SET is_deleted = 1,
                   updated_at = ?,
                   deleted_at = ?,
                   deleted_reason = ?
             WHERE id IN (SELECT id FROM memory_retention_ids)
            """,
            (now_iso, now_iso, _DELETE_REASON),
        )
        conn.execute(
            "DELETE FROM memory_fts WHERE id IN (SELECT id FROM memory_retention_ids)"
        )
        return removed_edges
    finally:
        conn.execute("DROP TABLE IF EXISTS memory_retention_ids")


def _unsupported_report(
    *,
    store: Any,
    cutoff: datetime,
) -> RuntimeMemoryRetentionReport:
    return RuntimeMemoryRetentionReport(
        status="unsupported",
        cutoff=cutoff.isoformat(),
        unsupported_reason=f"unsupported_store:{type(store).__name__}",
    )


def _policy_details(policy: RuntimeMemoryRetentionPolicy) -> dict[str, Any]:
    return {
        "log_retention_days": int(policy.log_retention_days),
        "patch_retention_count": int(policy.patch_retention_count),
    }


def _append_audit_event(store: Any, report: RuntimeMemoryRetentionReport) -> None:
    append = getattr(store, "append_audit_event", None)
    if append is None:
        return
    details = {
        "status": report.status,
        "cutoff": report.cutoff,
        "eligible_ids": list(report.eligible_record_ids),
        "retained_ids": list(report.retained_record_ids),
        "deleted_ids": list(report.deleted_record_ids),
        "eligible_count": report.eligible_count,
        "deleted_count": report.deleted_count,
        "unsupported_reason": report.unsupported_reason,
        **dict(report.details),
    }
    append(
        MemoryAuditEvent(
            event_type=MEMORY_RETENTION_ENFORCE,
            target_kind="memory_retention",
            details=details,
        )
    )


__all__ = [
    "RuntimeMemoryRetentionPolicy",
    "RuntimeMemoryRetentionReport",
    "dry_run_runtime_memory_retention",
    "enforce_runtime_memory_retention",
]
