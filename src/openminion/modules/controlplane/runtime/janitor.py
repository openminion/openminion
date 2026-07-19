"""Retention cleanup for controlplane runtime tables."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .audit import emit_audit_event

_TERMINAL_OUTBOX_STATUSES = ("sent", "dead")
_TERMINAL_WIZARD_STATES = ("DONE", "CANCELLED", "TIMEOUT", "FAILED")


@dataclass(frozen=True)
class ControlPlaneRetentionPolicy:
    audit_retention_days: int = 30
    outbox_terminal_retention_days: int = 7
    pair_token_retention_days: int = 30
    pair_attempt_retention_days: int = 90
    rate_limit_retention_days: int = 7
    wizard_terminal_retention_days: int = 30


@dataclass(frozen=True)
class ControlPlaneJanitorResult:
    deleted: dict[str, int]
    dry_run: bool

    def to_dict(self) -> dict[str, Any]:
        return {"deleted": dict(self.deleted), "dry_run": self.dry_run}


@dataclass
class ControlPlaneJanitor:
    store: Any
    policy: ControlPlaneRetentionPolicy = field(default_factory=ControlPlaneRetentionPolicy)
    audit_logger: object | None = None
    dry_run: bool = False

    def run_once(self) -> ControlPlaneJanitorResult:
        plans = _delete_plans(self.policy)
        deleted: dict[str, int] = {}
        for table, sql, params in plans:
            deleted[table] = self._count_or_delete(sql, params=params)
        result = ControlPlaneJanitorResult(deleted=deleted, dry_run=self.dry_run)
        emit_audit_event(
            self.audit_logger,
            "cp.janitor.cycle.completed",
            deleted=result.deleted,
            dry_run=result.dry_run,
        )
        return result

    def _count_or_delete(self, sql: str, *, params: tuple[Any, ...]) -> int:
        if self.dry_run:
            return _execute_count_query(self.store, _count_sql_for_delete(sql), params)
        return _execute_delete(self.store, sql, params)


class ControlPlaneJanitorSidecar:
    def __init__(
        self,
        *,
        janitor: ControlPlaneJanitor,
        interval_seconds: int = 3600,
        run_once: bool = False,
    ) -> None:
        self._janitor = janitor
        self._interval_seconds = max(1, int(interval_seconds))
        self._run_once = run_once
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_result: ControlPlaneJanitorResult | None = None
        self._last_error: str | None = None

    def status(self) -> dict[str, Any]:
        return {
            "ok": self._last_error is None,
            "pid_alive": self._thread is not None and self._thread.is_alive(),
            "last_result": self._last_result.to_dict() if self._last_result else None,
            "last_error": self._last_error,
            "interval_seconds": self._interval_seconds,
        }

    def start(self) -> dict[str, Any]:
        if self._thread is not None and self._thread.is_alive():
            return self.status()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="controlplane-janitor",
            daemon=True,
        )
        self._thread.start()
        return self.status()

    def stop(self, *, kill: bool = False) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.1 if kill else min(2.0, self._interval_seconds))
        return self.status() | {"stopped": True}

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._last_result = self._janitor.run_once()
                self._last_error = None
            except (AttributeError, RuntimeError, sqlite3.DatabaseError) as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
            if self._run_once:
                return
            self._stop.wait(self._interval_seconds)


def _delete_plans(
    policy: ControlPlaneRetentionPolicy,
) -> tuple[tuple[str, str, tuple[Any, ...]], ...]:
    audit_cutoff = _iso_days_ago(policy.audit_retention_days)
    outbox_cutoff = _iso_days_ago(policy.outbox_terminal_retention_days)
    rate_limit_cutoff = _iso_days_ago(policy.rate_limit_retention_days)
    pair_token_cutoff = _epoch_days_ago(policy.pair_token_retention_days)
    pair_attempt_cutoff = _epoch_days_ago(policy.pair_attempt_retention_days)
    wizard_cutoff = _epoch_days_ago(policy.wizard_terminal_retention_days)
    return (
        (
            "cp_audit_events",
            "DELETE FROM cp_audit_events WHERE timestamp < ?",
            (audit_cutoff,),
        ),
        (
            "cp_outbox",
            "DELETE FROM cp_outbox WHERE status IN (?, ?) AND created_at < ?",
            (*_TERMINAL_OUTBOX_STATUSES, outbox_cutoff),
        ),
        (
            "cp_pair_tokens",
            "DELETE FROM cp_pair_tokens WHERE (used_at_ts IS NOT NULL OR expires_at_ts < ?) AND created_at_ts < ?",
            (pair_token_cutoff, pair_token_cutoff),
        ),
        (
            "cp_pair_attempts",
            "DELETE FROM cp_pair_attempts WHERE attempted_at_ts < ?",
            (pair_attempt_cutoff,),
        ),
        (
            "cp_rate_limits",
            "DELETE FROM cp_rate_limits WHERE updated_at < ?",
            (rate_limit_cutoff,),
        ),
        (
            "cp_wizard_sessions",
            "DELETE FROM cp_wizard_sessions WHERE state IN (?, ?, ?, ?) AND updated_at_ts < ?",
            (*_TERMINAL_WIZARD_STATES, wizard_cutoff),
        ),
    )


def _execute_delete(store: Any, sql: str, params: tuple[Any, ...]) -> int:
    executor = getattr(store, "_execute_count", None)
    if callable(executor):
        try:
            return int(executor(sql, params))
        except (sqlite3.DatabaseError, RuntimeError, AttributeError):
            return 0
    record_store = getattr(store, "_rs", None)
    executor = getattr(record_store, "execute_count", None)
    if callable(executor):
        try:
            return int(executor(sql, params))
        except (sqlite3.DatabaseError, RuntimeError, AttributeError):
            return 0
    return 0


def _execute_count_query(store: Any, sql: str, params: tuple[Any, ...]) -> int:
    query = getattr(store, "_query_dicts", None)
    if callable(query):
        try:
            rows = query(sql, params)
            return int(rows[0].get("count", 0) if rows else 0)
        except (sqlite3.DatabaseError, RuntimeError, AttributeError):
            return 0
    record_store = getattr(store, "_rs", None)
    query = getattr(record_store, "query_dicts", None)
    if callable(query):
        try:
            rows = query(sql, params)
            return int(rows[0].get("count", 0) if rows else 0)
        except (sqlite3.DatabaseError, RuntimeError, AttributeError):
            return 0
    return 0


def _count_sql_for_delete(delete_sql: str) -> str:
    table = delete_sql.split(" FROM ", 1)[1].split(" WHERE ", 1)[0].strip()
    where = delete_sql.split(" WHERE ", 1)[1]
    return f"SELECT COUNT(*) AS count FROM {table} WHERE {where}"


def _iso_days_ago(days: int) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, int(days)))
    return cutoff.isoformat().replace("+00:00", "Z")


def _epoch_days_ago(days: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, int(days)))
    return int(cutoff.timestamp())


__all__ = [
    "ControlPlaneJanitor",
    "ControlPlaneJanitorResult",
    "ControlPlaneJanitorSidecar",
    "ControlPlaneRetentionPolicy",
]
