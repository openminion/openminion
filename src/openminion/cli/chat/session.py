from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

from openminion.cli.config import resolve_cli_roots
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import (
    DEFAULT_DATABASE_PATH,
    connect_database,
)


_T = TypeVar("_T")


@dataclass
class _EventSource:
    conn: sqlite3.Connection
    session_id: str
    table: str
    seq_col: str
    ts_col: str
    order_col: str

    def latest_event_payload(self, event_type: str) -> dict[str, Any] | None:
        return _latest_event_payload(
            conn=self.conn,
            session_id=self.session_id,
            table=self.table,
            seq_col=self.seq_col,
            ts_col=self.ts_col,
            order_col=self.order_col,
            event_type=event_type,
        )


def load_session_debug_snapshot(
    *, storage_path: str, session_id: str
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "storage_path": storage_path,
        "session_id": session_id,
        "available": False,
    }
    if not storage_path:
        result["error"] = "storage_path_not_configured"
        return result

    db_path = Path(storage_path).expanduser()
    if not db_path.exists():
        result["error"] = "storage_file_not_found"
        result["resolved_path"] = str(db_path)
        return result

    from openminion.modules.storage.record_store import RecordStoreSQLite

    store = RecordStoreSQLite(db_path, wal=True)
    conn = store.connection
    try:
        source = _event_source_spec(conn, session_id=session_id)
        if source is None:
            result["error"] = "no_supported_event_table"
            result["resolved_path"] = str(db_path)
            return result
        table = str(source["table"])
        seq_col = str(source["seq_col"])
        ts_col = str(source["ts_col"])
        order_col = str(source["order_col"])
        result["event_source"] = table
        src = _EventSource(
            conn=conn,
            session_id=session_id,
            table=table,
            seq_col=seq_col,
            ts_col=ts_col,
            order_col=order_col,
        )

        memory_events = (
            "memory.context.built",
            "memory.context.failed",
            "memory.retrieval.built",
            "memory.turn.recorded",
            "memory.turn.record_failed",
            "memory.capsule.refreshed",
            "memory.capsule.refresh_skipped",
            "memory.capsule.refresh_failed",
        )
        interesting = (
            "llm.call.started",
            "context.manifest.created",
            "llm.call.completed",
            "tool.request",
            "tool.completed",
            "summary.updated",
            "session.compaction.archive",
            "compression.checkpoint.created",
            "compression.checkpoint.failed",
            *memory_events,
        )
        placeholders = ",".join("?" for _ in interesting)
        rows = conn.execute(
            f"""
            SELECT event_type, COUNT(*) AS count
            FROM {table}
            WHERE session_id = ?
              AND event_type IN ({placeholders})
            GROUP BY event_type
            ORDER BY event_type
            """,
            (session_id, *interesting),
        ).fetchall()
        event_counts = {str(row["event_type"]): int(row["count"]) for row in rows}
        result["event_counts"] = event_counts
        result["memory_event_counts"] = {
            event_type: int(event_counts.get(event_type, 0))
            for event_type in memory_events
        }
        result["event_tail"] = _event_tail(
            conn=conn,
            session_id=session_id,
            table=table,
            seq_col=seq_col,
            ts_col=ts_col,
            order_col=order_col,
            limit=25,
        )

        manifest_row = conn.execute(
            f"""
            SELECT {seq_col} AS seq, {ts_col} AS timestamp, payload_json
            FROM {table}
            WHERE session_id = ? AND event_type = 'context.manifest.created'
            ORDER BY {order_col} DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        result["latest_manifest"] = _manifest_debug_payload(manifest_row)

        result["llm_call_order_tail"] = _llm_call_order_tail(
            conn=conn,
            session_id=session_id,
            table=table,
            seq_col=seq_col,
            order_col=order_col,
            limit=20,
        )

        result["latest_summary_update"] = src.latest_event_payload("summary.updated")
        result["latest_compaction_archive"] = src.latest_event_payload(
            "session.compaction.archive"
        )
        result["latest_checkpoint_created"] = src.latest_event_payload(
            "compression.checkpoint.created"
        )
        result["latest_checkpoint_failed"] = src.latest_event_payload(
            "compression.checkpoint.failed"
        )
        result["memory_trace"] = {
            "latest_context_built": src.latest_event_payload("memory.context.built"),
            "latest_retrieval_built": src.latest_event_payload(
                "memory.retrieval.built"
            ),
            "latest_turn_recorded": src.latest_event_payload("memory.turn.recorded"),
            "latest_turn_record_failed": src.latest_event_payload(
                "memory.turn.record_failed"
            ),
            "latest_capsule_refreshed": src.latest_event_payload(
                "memory.capsule.refreshed"
            ),
            "latest_capsule_refresh_skipped": src.latest_event_payload(
                "memory.capsule.refresh_skipped"
            ),
            "latest_capsule_refresh_failed": src.latest_event_payload(
                "memory.capsule.refresh_failed"
            ),
            "latest_context_failed": src.latest_event_payload("memory.context.failed"),
        }
        has_started = int(event_counts.get("llm.call.started", 0)) > 0
        has_manifest = int(event_counts.get("context.manifest.created", 0)) > 0
        has_completed = int(event_counts.get("llm.call.completed", 0)) > 0
        has_summary = int(event_counts.get("summary.updated", 0)) > 0
        has_archive = int(event_counts.get("session.compaction.archive", 0)) > 0
        has_checkpoint = int(event_counts.get("compression.checkpoint.created", 0)) > 0
        has_memory_trace = (
            int(event_counts.get("memory.context.built", 0)) > 0
            or int(event_counts.get("memory.turn.recorded", 0)) > 0
        )
        checks = {
            "llm_started": has_started,
            "context_manifest": has_manifest,
            "llm_completed": has_completed,
            "summary_updated": has_summary,
            "compaction_archive": has_archive,
            "compression_checkpoint": has_checkpoint,
            "llm_pipeline_complete": has_started and has_manifest and has_completed,
            "continuity_pipeline_active": has_summary or has_archive or has_checkpoint,
            "memory_trace_active": has_memory_trace,
        }
        missing = [
            name
            for name, enabled in checks.items()
            if not enabled
            and not name.endswith("_active")
            and not name.endswith("_complete")
        ]
        result["continuity_checks"] = checks
        result["missing_signals"] = missing
        result["messages"] = _messages_debug_snapshot(
            conn=conn, session_id=session_id, limit=12
        )
        result["session_context"] = _session_context_debug_snapshot(
            conn=conn, session_id=session_id
        )
        result["available"] = True
        result["resolved_path"] = str(db_path)
        return result
    except Exception as exc:  # pragma: no cover - defensive fallback
        result["error"] = str(exc)
        result["resolved_path"] = str(db_path)
        return result
    finally:
        store.close()


def _sqlite_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _event_source_spec(
    conn: sqlite3.Connection, *, session_id: str
) -> dict[str, str] | None:
    candidates: list[dict[str, str]] = [
        {
            "table": "session_events",
            "seq_col": "seq",
            "ts_col": "timestamp",
            "order_col": "seq",
        },
        {
            "table": "core_events",
            "seq_col": "event_id",
            "ts_col": "ts",
            "order_col": "ts",
        },
        {
            "table": "events",
            "seq_col": "id",
            "ts_col": "created_at",
            "order_col": "id",
        },
    ]
    available: list[dict[str, str]] = []
    for spec in candidates:
        table = str(spec["table"])
        if not _sqlite_table_exists(conn, table):
            continue
        available.append(spec)
        row = conn.execute(
            f"""
            SELECT 1
            FROM {table}
            WHERE session_id = ?
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if row is not None:
            return spec
    return available[0] if available else None


def _safe_json_object(raw: str | None) -> dict[str, Any]:
    if raw is None:
        return {}
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _manifest_debug_payload(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    payload = _safe_json_object(row["payload_json"])
    included = payload.get("included_segment_ids")
    dropped = payload.get("dropped_segment_ids")
    return {
        "seq": _normalize_event_seq(row["seq"]),
        "timestamp": str(row["timestamp"]),
        "llm_call_id": payload.get("llm_call_id"),
        "prompt_context_id": payload.get("prompt_context_id"),
        "pack_policy_used": payload.get("pack_policy_used"),
        "compressors_used": payload.get("compressors_used"),
        "included_segments": len(included) if isinstance(included, list) else 0,
        "dropped_segments": len(dropped) if isinstance(dropped, list) else 0,
    }


def _latest_event_payload(
    *,
    conn: sqlite3.Connection,
    session_id: str,
    table: str,
    seq_col: str,
    ts_col: str,
    order_col: str,
    event_type: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        f"""
        SELECT {seq_col} AS seq, {ts_col} AS timestamp, payload_json
        FROM {table}
        WHERE session_id = ? AND event_type = ?
        ORDER BY {order_col} DESC
        LIMIT 1
        """,
        (session_id, event_type),
    ).fetchone()
    if row is None:
        return None
    return {
        "seq": _normalize_event_seq(row["seq"]),
        "timestamp": str(row["timestamp"]),
        "payload": _safe_json_object(row["payload_json"]),
    }


def _llm_call_order_tail(
    *,
    conn: sqlite3.Connection,
    session_id: str,
    table: str,
    seq_col: str,
    order_col: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    safe_limit = max(1, int(limit))
    rows = conn.execute(
        f"""
        SELECT {seq_col} AS seq, event_type, payload_json
        FROM {table}
        WHERE session_id = ?
          AND event_type IN ('llm.call.started', 'context.manifest.created', 'llm.call.completed')
        ORDER BY {order_col} DESC
        LIMIT ?
        """,
        (session_id, safe_limit),
    ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    ordered_rows = list(reversed(rows))
    for position, row in enumerate(ordered_rows, start=1):
        payload = _safe_json_object(row["payload_json"])
        llm_call_id = str(payload.get("llm_call_id", "")).strip()
        if not llm_call_id:
            continue
        bucket = grouped.setdefault(
            llm_call_id,
            {
                "llm_call_id": llm_call_id,
                "started": 0,
                "manifest": 0,
                "completed": 0,
                "last_seq": "",
                "_last_position": 0,
            },
        )
        event_type = str(row["event_type"])
        if event_type == "llm.call.started":
            bucket["started"] += 1
        elif event_type == "context.manifest.created":
            bucket["manifest"] += 1
        elif event_type == "llm.call.completed":
            bucket["completed"] += 1
        bucket["last_seq"] = _normalize_event_seq(row["seq"])
        bucket["_last_position"] = position

    ordered = sorted(
        grouped.values(), key=lambda item: int(item["_last_position"]), reverse=True
    )
    for item in ordered:
        item.pop("_last_position", None)
    return ordered[:10]


def _event_tail(
    *,
    conn: sqlite3.Connection,
    session_id: str,
    table: str,
    seq_col: str,
    ts_col: str,
    order_col: str,
    limit: int = 25,
) -> list[dict[str, Any]]:
    safe_limit = max(1, int(limit))
    rows = conn.execute(
        f"""
        SELECT {seq_col} AS seq, {ts_col} AS timestamp, event_type
        FROM {table}
        WHERE session_id = ?
        ORDER BY {order_col} DESC
        LIMIT ?
        """,
        (session_id, safe_limit),
    ).fetchall()
    ordered = list(reversed(rows))
    return [
        {
            "seq": _normalize_event_seq(row["seq"]),
            "timestamp": str(row["timestamp"]),
            "event_type": str(row["event_type"]),
        }
        for row in ordered
    ]


def _normalize_event_seq(raw: Any) -> int | str:
    try:
        return int(raw)
    except (TypeError, ValueError):
        text = str(raw).strip()
        return text or "unknown"


def _messages_debug_snapshot(
    *,
    conn: sqlite3.Connection,
    session_id: str,
    limit: int = 12,
) -> dict[str, Any]:
    if not _sqlite_table_exists(conn, "messages"):
        return {"available": False, "reason": "messages_table_missing"}
    safe_limit = max(1, int(limit))
    count_row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM messages
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()
    total = int(count_row["c"]) if count_row is not None else 0
    rows = conn.execute(
        """
        SELECT role, body, created_at
        FROM messages
        WHERE session_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (session_id, safe_limit),
    ).fetchall()
    ordered = list(reversed(rows))
    tail = [
        {
            "role": str(row["role"]),
            "created_at": str(row["created_at"]),
            "preview": str(row["body"])[:180],
        }
        for row in ordered
    ]
    return {
        "available": True,
        "message_count": total,
        "tail": tail,
    }


def _session_context_debug_snapshot(
    *,
    conn: sqlite3.Connection,
    session_id: str,
) -> dict[str, Any]:
    if not _sqlite_table_exists(conn, "session_contexts"):
        return {"available": False, "reason": "session_contexts_table_missing"}
    row = conn.execute(
        """
        SELECT
          pinned_context,
          rolling_summary,
          compacted_until_rowid,
          compacted_until_created_at,
          compacted_until_message_id,
          compacted_message_count,
          created_at,
          updated_at
        FROM session_contexts
        WHERE session_id = ?
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return {"available": True, "present": False}
    rolling_summary = str(row["rolling_summary"] or "")
    pinned_context = str(row["pinned_context"] or "")
    return {
        "available": True,
        "present": True,
        "updated_at": str(row["updated_at"]),
        "rolling_summary_chars": len(rolling_summary),
        "rolling_summary_preview": rolling_summary[:180],
        "pinned_context_chars": len(pinned_context),
        "pinned_context_preview": pinned_context[:180],
        "compacted_until_rowid": row["compacted_until_rowid"],
        "compacted_until_created_at": str(row["compacted_until_created_at"] or ""),
        "compacted_until_message_id": str(row["compacted_until_message_id"] or ""),
        "compacted_message_count": row["compacted_message_count"],
    }


def with_session_store(
    *,
    config_path: str | None,
    default: _T,
    operation: Callable[[SessionStore], _T],
    runtime_state: Any | None = None,
    resolve_cli_roots_fn: Callable[..., Any] = resolve_cli_roots,
) -> _T:
    inproc_runtime = getattr(runtime_state, "inproc_runtime", None)
    if (
        inproc_runtime is not None
        and getattr(inproc_runtime, "sessions", None) is not None
    ):
        try:
            return operation(inproc_runtime.sessions)
        except (OSError, sqlite3.Error, RuntimeError, ValueError):
            return default

    try:
        roots = resolve_cli_roots_fn(
            config_path=config_path,
            fallback_to_cwd=True,
        )
        database_path = (roots.data_root / DEFAULT_DATABASE_PATH).resolve(strict=False)
        if not database_path.exists():
            return default
        connection = connect_database(database_path, env=roots.env)
        try:
            return operation(SessionStore(connection))
        finally:
            connection.close()
    except (OSError, sqlite3.Error, RuntimeError, ValueError):
        return default


def latest_session_conversation_id(
    *,
    session_id: str,
    config_path: str | None,
    resolve_cli_roots_fn: Callable[..., Any] = resolve_cli_roots,
) -> str:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return ""
    return with_session_store(
        config_path=config_path,
        default="",
        operation=lambda store: (
            store.latest_conversation_id(session_id=normalized_session_id) or ""
        ),
        resolve_cli_roots_fn=resolve_cli_roots_fn,
    )


def latest_session_agent_id(
    *,
    session_id: str,
    config_path: str | None,
    resolve_cli_roots_fn: Callable[..., Any] = resolve_cli_roots,
) -> str:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return ""
    events, messages = with_session_store(
        config_path=config_path,
        default=([], []),
        operation=lambda store: (
            store.list_events(
                session_id=normalized_session_id,
                limit=25,
                newest_first=True,
                event_type_prefix="client.",
            ),
            store.list_recent_messages(
                session_id=normalized_session_id,
                limit=25,
            ),
        ),
        resolve_cli_roots_fn=resolve_cli_roots_fn,
    )

    for event in events:
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            continue
        agent_id = str(
            payload.get("selected_profile_id")
            or payload.get("profile_agent_id")
            or payload.get("agent_id")
            or ""
        ).strip()
        if agent_id:
            return agent_id

    for message in reversed(messages):
        if str(getattr(message, "role", "") or "").strip().lower() != "outbound":
            continue
        metadata = getattr(message, "metadata", {}) or {}
        if not isinstance(metadata, dict):
            continue
        agent_id = str(metadata.get("agent") or metadata.get("agent_id") or "").strip()
        if agent_id:
            return agent_id
    active_agent = with_session_store(
        config_path=config_path,
        default="",
        operation=lambda store: store.get_active_agent(normalized_session_id) or "",
        resolve_cli_roots_fn=resolve_cli_roots_fn,
    )
    if active_agent:
        return active_agent
    return ""


def session_allows_agent_id(
    *,
    session_id: str,
    agent_id: str,
    config_path: str | None,
    resolve_cli_roots_fn: Callable[..., Any] = resolve_cli_roots,
) -> bool:
    normalized_session_id = str(session_id or "").strip()
    normalized_agent_id = str(agent_id or "").strip().lower()
    if not normalized_session_id or not normalized_agent_id:
        return False

    def _check(store: SessionStore) -> bool:
        session = store.get_session(normalized_session_id)
        if session is None:
            return False
        participant = store.get_participant(
            normalized_session_id,
            "agent",
            normalized_agent_id,
        )
        if participant is not None:
            return True
        current = store.get_active_agent(normalized_session_id)
        return str(current or "").strip().lower() == normalized_agent_id

    return with_session_store(
        config_path=config_path,
        default=False,
        operation=_check,
        resolve_cli_roots_fn=resolve_cli_roots_fn,
    )


def local_human_post_block_reason(
    *,
    session_id: str,
    config_path: str | None,
    resolve_cli_roots_fn: Callable[..., Any] = resolve_cli_roots,
) -> str:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return ""

    def _check(store: SessionStore) -> str:
        session = store.get_session(normalized_session_id)
        if session is None:
            return ""
        local_human_id = str(session.metadata.get("local_human_id", "") or "").strip()
        if not local_human_id:
            return ""
        participant = store.get_participant(
            normalized_session_id,
            "human",
            local_human_id,
        )
        if participant is None:
            return ""
        if str(participant.role or "").strip().lower() != "observer":
            return ""
        return (
            f"[chat] human participant '{local_human_id}' is an observer in this room "
            "and cannot post messages."
        )

    return with_session_store(
        config_path=config_path,
        default="",
        operation=_check,
        resolve_cli_roots_fn=resolve_cli_roots_fn,
    )


def get_session_record(
    *,
    session_id: str,
    config_path: str | None,
    resolve_cli_roots_fn: Callable[..., Any] = resolve_cli_roots,
):
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return None
    return with_session_store(
        config_path=config_path,
        default=None,
        operation=lambda store: store.get_session(normalized_session_id),
        resolve_cli_roots_fn=resolve_cli_roots_fn,
    )


def close_session_record(
    *,
    session_id: str,
    config_path: str | None,
    reason: str,
    resolve_cli_roots_fn: Callable[..., Any] = resolve_cli_roots,
) -> bool:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return False
    return with_session_store(
        config_path=config_path,
        default=False,
        operation=lambda store: bool(
            store.close_session(session_id=normalized_session_id, reason=reason)
        ),
        resolve_cli_roots_fn=resolve_cli_roots_fn,
    )


def mark_stale_cli_sessions(
    *,
    config_path: str | None,
    timeout_seconds: int,
    resolve_cli_roots_fn: Callable[..., Any] = resolve_cli_roots,
) -> int:
    return with_session_store(
        config_path=config_path,
        default=0,
        operation=lambda store: store.mark_stale_sessions(
            timeout_seconds=timeout_seconds
        ),
        resolve_cli_roots_fn=resolve_cli_roots_fn,
    )


def ensure_cli_session_record(
    *,
    session_id: str,
    agent_id: str,
    config_path: str | None,
    resolve_cli_roots_fn: Callable[..., Any] = resolve_cli_roots,
) -> bool:
    normalized_session_id = str(session_id or "").strip()
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_session_id or not normalized_agent_id:
        return False
    return with_session_store(
        config_path=config_path,
        default=False,
        operation=lambda store: bool(
            store.resolve_session(
                agent_id=normalized_agent_id,
                channel="console",
                target="cli-chat",
                session_id=normalized_session_id,
            )
        ),
        resolve_cli_roots_fn=resolve_cli_roots_fn,
    )


def session_auto_name_from_text(text: str, *, max_chars: int) -> str:
    compact = " ".join(str(text or "").strip().split())
    return compact[:max_chars].rstrip()


def set_session_name_if_missing(
    *,
    session_id: str,
    config_path: str | None,
    name: str,
    max_chars: int,
    resolve_cli_roots_fn: Callable[..., Any] = resolve_cli_roots,
) -> bool:
    candidate = session_auto_name_from_text(name, max_chars=max_chars)
    if not candidate:
        return False

    def _update(store: SessionStore) -> bool:
        session = store.get_session(session_id)
        if session is None:
            return False
        current_name = str(session.metadata.get("name", "") or "").strip()
        if current_name:
            return False
        store.update_session_metadata(session_id=session_id, patch={"name": candidate})
        return True

    return with_session_store(
        config_path=config_path,
        default=False,
        operation=_update,
        resolve_cli_roots_fn=resolve_cli_roots_fn,
    )


def maybe_auto_name_session(
    *,
    session_id: str,
    config_path: str | None,
    first_user_text: str,
    max_chars: int,
    resolve_cli_roots_fn: Callable[..., Any] = resolve_cli_roots,
) -> bool:
    candidate = session_auto_name_from_text(first_user_text, max_chars=max_chars)
    if not candidate:
        return False

    def _update(store: SessionStore) -> bool:
        session = store.get_session(session_id)
        if session is None:
            return False
        current_name = str(session.metadata.get("name", "") or "").strip()
        if current_name:
            return False
        messages = store.list_messages(session_id=session_id, limit=4)
        first_inbound = next(
            (
                message
                for message in messages
                if str(message.role or "").strip().lower() in {"inbound", "user"}
            ),
            None,
        )
        has_outbound = any(
            str(message.role or "").strip().lower()
            in {"outbound", "assistant", "agent"}
            for message in messages
        )
        if first_inbound is None or not has_outbound:
            return False
        if str(first_inbound.body or "").strip() != str(first_user_text or "").strip():
            return False
        store.update_session_metadata(session_id=session_id, patch={"name": candidate})
        return True

    return with_session_store(
        config_path=config_path,
        default=False,
        operation=_update,
        resolve_cli_roots_fn=resolve_cli_roots_fn,
    )
