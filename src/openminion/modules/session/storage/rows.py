"""Session storage row decoders."""

import sqlite3
from typing import Any
from collections.abc import Mapping

from .json_utils import parse_json


RowLike = sqlite3.Row | Mapping[str, Any]


def _row_value(row: RowLike, key: str) -> Any:
    return row[key]


def _json_value(row: RowLike, key: str, fallback: Any) -> Any:
    return parse_json(str(_row_value(row, key)), fallback)


def row_to_session(row: RowLike) -> dict[str, Any]:
    return {
        "session_id": str(_row_value(row, "session_id")),
        "created_at": str(_row_value(row, "created_at")),
        "updated_at": str(_row_value(row, "updated_at")),
        "title": _row_value(row, "title"),
        "status": str(_row_value(row, "status")),
        "active_agent_id": _row_value(row, "active_agent_id"),
        "active_profile_version": _row_value(row, "active_profile_version"),
        "participants": _json_value(row, "participants_json", []),
        "root_goal": _row_value(row, "root_goal"),
        "tags": _json_value(row, "tags_json", []),
        "config_snapshot_ref": _row_value(row, "config_snapshot_ref"),
        "meta": _json_value(row, "meta_json", {}),
    }


def row_to_turn(row: RowLike) -> dict[str, Any]:
    return {
        "turn_id": str(_row_value(row, "turn_id")),
        "session_id": str(_row_value(row, "session_id")),
        "ts": str(_row_value(row, "ts")),
        "role": str(_row_value(row, "role")),
        "content": str(_row_value(row, "content")),
        "attachments": _json_value(row, "attachments_json", []),
        "meta": _json_value(row, "meta_json", {}),
    }


def row_to_session_event(row: RowLike) -> dict[str, Any]:
    return {
        "event_id": str(_row_value(row, "event_id")),
        "session_id": str(_row_value(row, "session_id")),
        "seq": int(_row_value(row, "seq")),
        "timestamp": str(_row_value(row, "timestamp")),
        "event_type": str(_row_value(row, "event_type")),
        "actor_type": str(_row_value(row, "actor_type")),
        "actor_id": _row_value(row, "actor_id"),
        "trace_id": _row_value(row, "trace_id"),
        "span_id": _row_value(row, "span_id"),
        "task_id": _row_value(row, "task_id"),
        "parent_event_id": _row_value(row, "parent_event_id"),
        "payload": _json_value(row, "payload_json", {}),
        "refs": _json_value(row, "refs_json", {}),
        "importance": int(_row_value(row, "importance")),
        "redaction": str(_row_value(row, "redaction")),
    }


def row_to_event_compat_from_session_event(row: RowLike) -> dict[str, Any]:
    payload = _json_value(row, "payload_json", {})
    refs = _json_value(row, "refs_json", {})
    artifact_refs = refs.get("artifact_refs", [])
    if not isinstance(artifact_refs, list):
        artifact_refs = []
    memory_refs = refs.get("memory_refs", [])
    if not isinstance(memory_refs, list):
        memory_refs = []
    return {
        "event_id": str(_row_value(row, "event_id")),
        "session_id": str(_row_value(row, "session_id")),
        "ts": str(_row_value(row, "timestamp")),
        "type": str(_row_value(row, "event_type")),
        "agent_id": _row_value(row, "actor_id"),
        "trace_id": _row_value(row, "trace_id"),
        "task_id": _row_value(row, "task_id") or payload.get("task_id"),
        "parent_id": _row_value(row, "parent_event_id"),
        "payload": payload,
        "artifact_refs": artifact_refs,
        "memory_refs": memory_refs,
        "status": payload.get("status"),
        "error": payload.get("error"),
    }


def row_to_cron_job(row: RowLike) -> dict[str, Any]:
    return {
        "job_id": str(_row_value(row, "job_id")),
        "name": str(_row_value(row, "name")),
        "description": _row_value(row, "description"),
        "enabled": bool(int(_row_value(row, "enabled"))),
        "agent_id": _row_value(row, "agent_id"),
        "schedule": _json_value(row, "schedule_json", {}),
        "payload": _json_value(row, "payload_json", {}),
        "delivery": _json_value(row, "delivery_json", {"mode": "none"}),
        "session_target": str(_row_value(row, "session_target")),
        "wake_mode": str(_row_value(row, "wake_mode")),
        "delete_after_run": bool(int(_row_value(row, "delete_after_run"))),
        "misfire_policy": str(_row_value(row, "misfire_policy")),
        "max_lateness_s": int(_row_value(row, "max_lateness_s")),
        "max_concurrency": int(_row_value(row, "max_concurrency")),
        "next_due_at": _row_value(row, "next_due_at"),
        "last_run_at": _row_value(row, "last_run_at"),
        "created_at": str(_row_value(row, "created_at")),
        "updated_at": str(_row_value(row, "updated_at")),
    }


def row_to_cron_run(row: RowLike) -> dict[str, Any]:
    return {
        "run_id": str(_row_value(row, "run_id")),
        "job_id": _row_value(row, "job_id"),
        "state": str(_row_value(row, "state")),
        "due_at": str(_row_value(row, "due_at")),
        "started_at": _row_value(row, "started_at"),
        "finished_at": _row_value(row, "finished_at"),
        "isolated_session_id": _row_value(row, "isolated_session_id"),
        "summary": _row_value(row, "summary"),
        "artifact_refs": _json_value(row, "artifact_refs_json", []),
        "error": (
            _json_value(row, "error_json", {})
            if _row_value(row, "error_json")
            else None
        ),
        "lease_owner": _row_value(row, "lease_owner"),
        "lease_expires_at": _row_value(row, "lease_expires_at"),
        "delivery_targets": _json_value(row, "delivery_targets_json", []),
        "attempts": int(_row_value(row, "attempts")),
        "created_at": str(_row_value(row, "created_at")),
        "updated_at": str(_row_value(row, "updated_at")),
    }
