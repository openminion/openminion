from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from openminion.modules.storage.record_store import RecordStore
from openminion.modules.task.plan import (
    TaskPlan,
    TaskPlanStepBlocked,
    TaskPlanStepCompleted,
    TaskPlanTerminalSignal,
)

from threading import RLock

from .json_utils import parse_json, to_json
from .rows import (
    row_to_event_compat_from_session_event,
    row_to_session_event,
    row_to_turn,
)


def _apply_step_completed(
    plan: TaskPlan,
    completed: TaskPlanStepCompleted,
) -> TaskPlan:
    steps = []
    for step in plan.steps:
        if step.step_id != completed.step_id:
            steps.append(step)
            continue
        steps.append(
            step.model_copy(
                update={
                    "status": "completed",
                    "output_summary": completed.output_summary,
                    "blocker_type": None,
                    "blocker_details": None,
                }
            )
        )
    return plan.model_copy(update={"steps": steps})


def _apply_step_blocked(plan: TaskPlan, blocked: TaskPlanStepBlocked) -> TaskPlan:
    steps = []
    for step in plan.steps:
        if step.step_id != blocked.step_id:
            steps.append(step)
            continue
        steps.append(
            step.model_copy(
                update={
                    "status": "blocked",
                    "blocker_type": blocked.blocker_type,
                    "blocker_details": blocked.blocker_details,
                }
            )
        )
    return plan.model_copy(update={"steps": steps})


class EventStore:
    def __init__(
        self,
        record_store: RecordStore,
    ) -> None:
        self._rs = record_store

    def insert_session_event_tx(
        self,
        *,
        session_id: str,
        event_type: str,
        actor_type: str,
        actor_id: str | None,
        trace_id: str | None,
        span_id: str | None,
        task_id: str | None,
        parent_event_id: str | None,
        payload: dict[str, Any] | None,
        refs: dict[str, Any] | None,
        importance: int,
        redaction: str,
        event_id: str | None = None,
        timestamp: str | None = None,
    ) -> str:
        rows = self._rs.query_dicts(
            "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM session_events WHERE session_id = ?",
            (session_id,),
        )
        row = rows[0] if rows else None
        next_seq = int(row["max_seq"]) + 1 if row is not None else 1
        event_id_value = event_id or uuid4().hex
        timestamp_value = timestamp or datetime.now(timezone.utc).isoformat()
        self._rs.execute_count(
            """
            INSERT INTO session_events(
              event_id, session_id, seq, timestamp, event_type, actor_type, actor_id,
              trace_id, span_id, task_id, parent_event_id, payload_json, refs_json,
              importance, redaction
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id_value,
                session_id,
                next_seq,
                timestamp_value,
                event_type,
                actor_type,
                actor_id,
                trace_id,
                span_id,
                task_id,
                parent_event_id,
                to_json(dict(payload or {})),
                to_json(dict(refs or {})) if refs is not None else None,
                self._clamp_importance(importance),
                redaction,
            ),
        )
        return event_id_value

    def get_events(
        self,
        session_id: str,
        *,
        after_seq: int | None = None,
        types: list[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["session_id = ?"]
        params: list[Any] = [session_id]
        if after_seq is not None:
            clauses.append("seq > ?")
            params.append(max(0, int(after_seq)))
        normalized_types = [str(item) for item in (types or []) if str(item).strip()]
        if normalized_types:
            placeholders = ",".join("?" for _ in normalized_types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(normalized_types)
        query = f"""
            SELECT event_id, session_id, seq, timestamp, event_type, actor_type, actor_id,
                   trace_id, span_id, task_id, parent_event_id, payload_json, refs_json,
                   importance, redaction
            FROM session_events
            WHERE {" AND ".join(clauses)}
            ORDER BY seq ASC
        """
        if limit is not None:
            query += " LIMIT ?"
            params.append(max(1, int(limit)))
        rows = self._rs.query_dicts(query, tuple(params))
        return [row_to_session_event(row) for row in rows]

    def get_event_by_id(self, event_id: str) -> dict[str, Any] | None:
        rows = self._rs.query_dicts(
            """
            SELECT event_id, session_id, seq, timestamp, event_type, actor_type, actor_id,
                   trace_id, span_id, task_id, parent_event_id, payload_json, refs_json,
                   importance, redaction
            FROM session_events
            WHERE event_id = ?
            """,
            (event_id,),
        )
        return row_to_session_event(rows[0]) if rows else None

    def get_events_by_parent_and_type(
        self,
        parent_event_id: str,
        event_type: str,
    ) -> list[dict[str, Any]]:
        rows = self._rs.query_dicts(
            """
            SELECT event_id, session_id, seq, timestamp, event_type, actor_type, actor_id,
                   trace_id, span_id, task_id, parent_event_id, payload_json, refs_json,
                   importance, redaction
            FROM session_events
            WHERE parent_event_id = ? AND event_type = ?
            ORDER BY timestamp ASC, event_id ASC
            """,
            (parent_event_id, event_type),
        )
        return [row_to_session_event(row) for row in rows]

    def get_latest_continuation_projection(
        self,
        session_id: str,
    ) -> dict[str, Any] | None:
        events = self.get_events(
            session_id,
            types=["session.continuation.applied"],
        )
        if not events:
            return None
        payload = events[-1].get("payload")
        return dict(payload) if isinstance(payload, dict) else None

    def get_recent_tool_events(
        self, session_id: str, limit: int
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, int(limit))
        rows = self._rs.query_dicts(
            """
            SELECT event_id, session_id, seq, timestamp, event_type, actor_type, actor_id,
                   trace_id, span_id, task_id, parent_event_id, payload_json, refs_json,
                   importance, redaction
            FROM session_events
            WHERE session_id = ?
              AND (
                event_type LIKE 'tool.call.%'
                OR event_type LIKE 'tool.%'
              )
            ORDER BY seq DESC
            LIMIT ?
            """,
            (session_id, safe_limit),
        )
        items = [row_to_session_event(row) for row in rows]
        items.reverse()
        return [self._to_recent_tool_event(item) for item in items]

    def get_total_turn_count(self, session_id: str) -> int:
        rows = self._rs.query_dicts(
            """
            SELECT COUNT(*) AS count
            FROM session_events
            WHERE session_id = ?
              AND event_type IN ('turn.user', 'turn.assistant')
            """,
            (session_id,),
        )
        row = rows[0] if rows else None
        return int(row["count"]) if row is not None else 0

    def get_conversation_summary(
        self,
        session_id: str,
        *,
        limit_records: int = 24,
    ) -> str:
        safe_limit = max(1, int(limit_records))
        rows = self._rs.query_dicts(
            """
            SELECT event_id, session_id, seq, timestamp, event_type, actor_type, actor_id,
                   trace_id, span_id, task_id, parent_event_id, payload_json, refs_json,
                   importance, redaction
            FROM session_events
            WHERE session_id = ?
              AND (
                event_type IN ('turn.user', 'turn.assistant', 'turn.outcome')
                OR event_type LIKE 'tool.%'
              )
            ORDER BY seq ASC
            """,
            (session_id,),
        )
        events = [row_to_session_event(row) for row in rows]
        records: list[str] = []
        current_user = ""
        current_tool_families: list[str] = []
        pending_assistants: list[dict[str, Any]] = []

        def _payload(event: dict[str, Any]) -> dict[str, Any]:
            payload = event.get("payload")
            return payload if isinstance(payload, dict) else {}

        def _words_prefix(text: str, count: int) -> str:
            return " ".join(str(text or "").split()[:count])

        def _words_suffix(text: str, count: int) -> str:
            return " ".join(str(text or "").split()[-count:])

        def _tool_family(tool_name: str) -> str:
            normalized = str(tool_name or "").strip()
            if not normalized:
                return ""
            return normalized.split(".", 1)[0]

        def _emit_pending(route_type: str = "unknown") -> None:
            while pending_assistants:
                item = pending_assistants.pop(0)
                families = sorted(set(item["tool_families"]))
                records.append(
                    "turn_index={turn_index}; user_preview={user_preview}; "
                    "route_type={route_type}; assistant_response_tokens={tokens}; "
                    "tool_families_used={families}; assistant_tail_preview={tail}".format(
                        turn_index=item["turn_index"],
                        user_preview=json.dumps(
                            item["user_preview"], ensure_ascii=True
                        ),
                        route_type=json.dumps(str(route_type or "unknown")),
                        tokens=item["tokens"],
                        families=json.dumps(families, ensure_ascii=True),
                        tail=json.dumps(item["tail"], ensure_ascii=True),
                    )
                )

        for event in events:
            event_type = str(event.get("event_type") or "")
            payload = _payload(event)
            if event_type == "turn.user":
                _emit_pending()
                current_user = str(payload.get("text") or "")
                current_tool_families = []
                continue
            if event_type.startswith("tool."):
                tool_name = str(payload.get("tool_name") or payload.get("tool") or "")
                family = _tool_family(tool_name)
                if family:
                    current_tool_families.append(family)
                continue
            if event_type == "turn.assistant":
                assistant_text = str(payload.get("text") or "")
                pending_assistants.append(
                    {
                        "turn_index": len(records) + len(pending_assistants) + 1,
                        "user_preview": _words_prefix(current_user, 10),
                        "tokens": max(1, len(assistant_text) // 4),
                        "tool_families": list(current_tool_families),
                        "tail": _words_suffix(assistant_text, 15),
                    }
                )
                current_tool_families = []
                continue
            if event_type == "turn.outcome":
                route_type = str(
                    payload.get("mode_name") or payload.get("status") or ""
                )
                _emit_pending(route_type or "unknown")
        _emit_pending()
        return "\n".join(records[-safe_limit:])

    def get_active_task_plan(self, session_id: str) -> dict[str, Any] | None:
        rows = self._rs.query_dicts(
            """
            SELECT event_id, session_id, seq, timestamp, event_type, actor_type, actor_id,
                   trace_id, span_id, task_id, parent_event_id, payload_json, refs_json,
                   importance, redaction
            FROM session_events
            WHERE session_id = ?
              AND event_type LIKE 'task_plan.%'
            ORDER BY seq ASC
            """,
            (session_id,),
        )
        active_plan: TaskPlan | None = None
        for row in rows:
            event = row_to_session_event(row)
            payload = event.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            event_type = str(event.get("event_type") or "")
            try:
                if event_type == "task_plan.declared":
                    active_plan = TaskPlan.model_validate(
                        payload.get("plan")
                        if isinstance(payload.get("plan"), dict)
                        else payload
                    )
                    continue
                if active_plan is None:
                    continue
                if event_type == "task_plan.revised":
                    plan_payload = (
                        payload.get("plan")
                        if isinstance(payload.get("plan"), dict)
                        else payload
                    )
                    revised = TaskPlan.model_validate(plan_payload)
                    active_plan = revised if revised.status == "active" else None
                    continue
                if event_type == "task_plan.step_completed":
                    completed = TaskPlanStepCompleted.model_validate(payload)
                    if completed.plan_id != active_plan.plan_id:
                        continue
                    active_plan = _apply_step_completed(active_plan, completed)
                    continue
                if event_type == "task_plan.step_blocked":
                    blocked = TaskPlanStepBlocked.model_validate(payload)
                    if blocked.plan_id != active_plan.plan_id:
                        continue
                    active_plan = _apply_step_blocked(active_plan, blocked)
                    continue
                if event_type in {"task_plan.abandoned", "task_plan.completed"}:
                    terminal = TaskPlanTerminalSignal.model_validate(payload)
                    if terminal.plan_id == active_plan.plan_id:
                        active_plan = None
            except Exception:
                continue
        if active_plan is None or active_plan.status != "active":
            return None
        return active_plan.model_dump(mode="json")

    def get_pending_trailer_feedback(self, session_id: str) -> dict[str, Any] | None:
        """Return the latest unconsumed trailer.feedback_pending payload."""
        rows = self._rs.query_dicts(
            """
            SELECT event_id, session_id, seq, timestamp, event_type, actor_type, actor_id,
                   trace_id, span_id, task_id, parent_event_id, payload_json, refs_json,
                   importance, redaction
            FROM session_events
            WHERE session_id = ?
              AND event_type IN ('trailer.feedback_pending', 'trailer.feedback_surfaced')
            ORDER BY seq DESC
            """,
            (session_id,),
        )
        for row in rows:
            event = row_to_session_event(row)
            event_type = str(event.get("event_type") or "")
            if event_type == "trailer.feedback_surfaced":
                # Most recent feedback event is already consumed.
                return None
            if event_type == "trailer.feedback_pending":
                payload = event.get("payload")
                return payload if isinstance(payload, dict) else None
        return None

    def _to_recent_tool_event(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        refs = event.get("refs")
        if not isinstance(refs, dict):
            refs = {}

        tool_name = (
            str(
                payload.get("tool_name")
                or payload.get("tool")
                or event.get("event_type")
                or "tool"
            ).strip()
            or "tool"
        )

        raw_error = payload.get("error")
        if isinstance(raw_error, dict):
            error_text = str(
                raw_error.get("message") or raw_error.get("code") or ""
            ).strip()
        else:
            error_text = str(raw_error or "").strip()

        excerpt = str(
            payload.get("summary") or payload.get("message") or error_text or ""
        ).strip()
        if not excerpt:
            excerpt = (
                json.dumps(payload, sort_keys=True)[:240]
                if payload
                else str(event.get("event_type") or "tool")
            )

        raw_artifact_refs = refs.get("artifact_refs")
        artifact_refs = (
            [str(item) for item in raw_artifact_refs]
            if isinstance(raw_artifact_refs, list)
            else []
        )

        return {
            "event_id": str(event.get("event_id") or ""),
            "session_id": str(event.get("session_id") or ""),
            "seq": int(event.get("seq") or 0),
            "timestamp": str(event.get("timestamp") or ""),
            "event_type": str(event.get("event_type") or ""),
            "trace_id": event.get("trace_id"),
            "tool_name": tool_name,
            "excerpt": excerpt[:240],
            "artifact_refs": artifact_refs,
        }

    def get_recent_turns(
        self, session_id: str, limit_messages: int
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, int(limit_messages))
        rows = self._rs.query_dicts(
            """
            SELECT event_id, session_id, seq, timestamp, event_type, payload_json
            FROM session_events
            WHERE session_id = ?
              AND event_type IN ('turn.system', 'turn.user', 'turn.assistant', 'turn.tool')
            ORDER BY seq DESC
            LIMIT ?
            """,
            (session_id, safe_limit),
        )

        if not rows:
            return []

        items: list[dict[str, Any]] = []
        ordered_rows = sorted(rows, key=lambda r: int(r["seq"]))
        for row in ordered_rows:
            payload = parse_json(str(row["payload_json"]), {})
            event_type = str(row["event_type"])
            role = event_type.replace("turn.", "")
            text = payload.get("text")
            attachments = payload.get("attachments")
            if not isinstance(attachments, list):
                attachments = []
            channel_meta = payload.get("channel_meta")
            if not isinstance(channel_meta, dict):
                channel_meta = {}
            ui_hints = payload.get("ui_hints")
            if not isinstance(ui_hints, dict):
                ui_hints = {}
            items.append(
                {
                    "turn_id": payload.get("turn_id"),
                    "event_id": str(row["event_id"]),
                    "seq": int(row["seq"]),
                    "timestamp": str(row["timestamp"]),
                    "role": role,
                    "text": text,
                    "attachments": attachments,
                    "channel_meta": channel_meta,
                    "ui_hints": ui_hints,
                    "is_error": bool(
                        payload.get("is_error")
                        or ui_hints.get("is_error")
                        or channel_meta.get("is_error")
                    ),
                }
            )
        return items

    def list_turns(
        self,
        session_id: str,
        *,
        lock: RLock,
        limit: int | None = None,
        before_ts: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return turns for ``session_id`` ordered ascending by ts.

        Live owner for the facade ``SQLiteSessionStore.list_turns`` query (SSFP2-05).
        Preserves the original payload shape and closed-connection behavior.
        """
        safe_limit = max(1, int(limit or 100))
        with lock:
            if before_ts:
                rows = self._rs.query_dicts(
                    """
                    SELECT turn_id, session_id, ts, role, content, attachments_json, meta_json
                    FROM turns
                    WHERE session_id = ? AND ts < ?
                    ORDER BY ts DESC, turn_id DESC
                    LIMIT ?
                    """,
                    (session_id, before_ts, safe_limit),
                )
            else:
                rows = self._rs.query_dicts(
                    """
                    SELECT turn_id, session_id, ts, role, content, attachments_json, meta_json
                    FROM turns
                    WHERE session_id = ?
                    ORDER BY ts DESC, turn_id DESC
                    LIMIT ?
                    """,
                    (session_id, safe_limit),
                )
        turns = [row_to_turn(row) for row in rows]
        turns.reverse()
        return turns

    def list_events_compat(
        self,
        session_id: str,
        *,
        lock: RLock,
        event_type: str | None = None,
        trace_id: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return session events in the compat payload shape."""
        safe_limit = max(1, int(limit or 100))
        clauses = ["session_id = ?"]
        params: list[Any] = [session_id]
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if trace_id:
            clauses.append("trace_id = ?")
            params.append(trace_id)
        if agent_id:
            clauses.append("actor_id = ?")
            params.append(agent_id)
        query = f"""
            SELECT event_id, session_id, seq, timestamp, event_type, actor_type, actor_id,
                   trace_id, span_id, task_id, parent_event_id, payload_json, refs_json,
                   importance, redaction
            FROM session_events
            WHERE {" AND ".join(clauses)}
            ORDER BY seq DESC
        """
        if status is None:
            query += " LIMIT ?"
            params.append(safe_limit)

        with lock:
            rows = self._rs.query_dicts(query, tuple(params))

        items: list[dict[str, Any]] = []
        for row in rows:
            item = row_to_event_compat_from_session_event(row)
            if status is not None and str(item.get("status")) != str(status):
                continue
            items.append(item)

        items.reverse()
        if len(items) > safe_limit:
            return items[-safe_limit:]
        return items

    @staticmethod
    def _clamp_importance(value: int) -> int:
        return max(0, min(int(value), 3))
