"""Audit mode resolution and the storage-backed tool-runtime audit sink."""

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any
from collections.abc import Mapping

from openminion.base.time import utc_now_iso as iso_now
from openminion.base.config.env import EnvironmentConfig, resolve_environment_config

from ..constants import (
    TOOL_AUDIT_WRITE_MODE_DUAL,
    TOOL_AUDIT_WRITE_MODE_JSONL_ONLY,
    TOOL_AUDIT_WRITE_MODE_OFF,
    TOOL_AUDIT_WRITE_MODE_STORAGE_ONLY,
)
from .policy import Policy


__all__ = [
    "ToolRuntimeAuditSink",
    "resolve_tool_runtime_audit_mode",
    "audit_writes_jsonl",
    "audit_writes_storage",
]


_AUDIT_WRITE_MODE_ALIASES: dict[str, str] = {
    TOOL_AUDIT_WRITE_MODE_DUAL: TOOL_AUDIT_WRITE_MODE_DUAL,
    "both": TOOL_AUDIT_WRITE_MODE_DUAL,
    "default": TOOL_AUDIT_WRITE_MODE_DUAL,
    "jsonl": TOOL_AUDIT_WRITE_MODE_JSONL_ONLY,
    TOOL_AUDIT_WRITE_MODE_JSONL_ONLY: TOOL_AUDIT_WRITE_MODE_JSONL_ONLY,
    "legacy": TOOL_AUDIT_WRITE_MODE_JSONL_ONLY,
    "run_root": TOOL_AUDIT_WRITE_MODE_JSONL_ONLY,
    "storage": TOOL_AUDIT_WRITE_MODE_STORAGE_ONLY,
    TOOL_AUDIT_WRITE_MODE_STORAGE_ONLY: TOOL_AUDIT_WRITE_MODE_STORAGE_ONLY,
    "db": TOOL_AUDIT_WRITE_MODE_STORAGE_ONLY,
    "sqlite": TOOL_AUDIT_WRITE_MODE_STORAGE_ONLY,
    TOOL_AUDIT_WRITE_MODE_OFF: TOOL_AUDIT_WRITE_MODE_OFF,
    "none": TOOL_AUDIT_WRITE_MODE_OFF,
    "disabled": TOOL_AUDIT_WRITE_MODE_OFF,
}


def _normalize_audit_write_mode(raw: Any) -> str:
    token = str(raw or "").strip().lower()
    if not token:
        return TOOL_AUDIT_WRITE_MODE_DUAL
    return _AUDIT_WRITE_MODE_ALIASES.get(token, TOOL_AUDIT_WRITE_MODE_DUAL)


def resolve_tool_runtime_audit_mode(
    *,
    policy: Policy | None = None,
    context_metadata: Mapping[str, Any] | None = None,
    env: EnvironmentConfig | None = None,
) -> str:
    """Resolve audit write mode with explicit precedence and safe fallback."""

    context_payload: Mapping[str, Any] = (
        context_metadata if isinstance(context_metadata, Mapping) else {}
    )
    for key in ("tool_runtime_audit_mode", "audit_write_mode"):
        if key in context_payload:
            return _normalize_audit_write_mode(context_payload.get(key))

    env_mode = (env or resolve_environment_config()).get(
        "OPENMINION_TOOL_RUNTIME_AUDIT_MODE", ""
    )
    if env_mode:
        return _normalize_audit_write_mode(env_mode)

    policy_raw = getattr(policy, "raw", {}) if policy is not None else {}
    if isinstance(policy_raw, Mapping):
        policy_context = policy_raw.get("context_metadata")
        if isinstance(policy_context, Mapping):
            for key in ("tool_runtime_audit_mode", "audit_write_mode"):
                if key in policy_context:
                    return _normalize_audit_write_mode(policy_context.get(key))

        audit_cfg = policy_raw.get("audit")
        if isinstance(audit_cfg, Mapping):
            if "write_mode" in audit_cfg:
                return _normalize_audit_write_mode(audit_cfg.get("write_mode"))

    return TOOL_AUDIT_WRITE_MODE_DUAL


def audit_writes_jsonl(mode: str) -> bool:
    return mode in {TOOL_AUDIT_WRITE_MODE_DUAL, TOOL_AUDIT_WRITE_MODE_JSONL_ONLY}


def audit_writes_storage(mode: str) -> bool:
    return mode in {"dual", "storage_only"}


@dataclass
class ToolRuntimeAuditSink:
    """Storage-backed audit sink for tool runtime events."""

    db_path: Path
    _conn: sqlite3.Connection | None = None
    _lock: RLock = field(default_factory=RLock)

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        with self._lock:
            if self._conn is None:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tool_runtime_audit_events (
                        event_id TEXT PRIMARY KEY,
                        ts TEXT NOT NULL,
                        run_id TEXT,
                        run_root TEXT,
                        event_json TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_tool_runtime_audit_events_ts
                    ON tool_runtime_audit_events(ts)
                    """
                )
                conn.commit()
                self._conn = conn
        return self._conn

    def append_event(
        self, event: Mapping[str, Any], *, run_root: Path | None = None
    ) -> None:
        payload = dict(event or {})
        event_id = str(payload.get("event_id") or "").strip()
        if not event_id:
            raise ValueError(
                "event_id is required for audit sink"
            )  # allow-bare-raise: internal invariant — audit sink contract on caller payload
        ts = str(payload.get("ts") or iso_now())
        payload["event_id"] = event_id
        payload["ts"] = ts
        run_id = str(payload.get("run_id") or "").strip() or None
        resolved_run_root = str(run_root) if run_root is not None else None
        serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        conn = self._connect()
        with self._lock:
            conn.execute(
                """
                INSERT OR REPLACE INTO tool_runtime_audit_events(event_id, ts, run_id, run_root, event_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_id, ts, run_id, resolved_run_root, serialized),
            )
            conn.commit()
