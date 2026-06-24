from pathlib import Path
from typing import Any
import logging

from openminion.modules.brain.interfaces import BRAIN_ADAPTER_INTERFACE_VERSION
from openminion.modules.session.diagnostics.events import (
    emit_session_operation,
)
from openminion.modules.telemetry.events.module import emit_module_telemetry

_LOG = logging.getLogger(__name__)


class SessctlAdapter:
    """Adapter for the real SQLiteSessionStore."""

    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(
        self,
        target: str | Path | Any,
        *,
        artifactctl: Any | None = None,
        telemetryctl: Any | None = None,
    ) -> None:
        from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore

        if isinstance(target, (str, Path)):
            self.store = SQLiteSessionStore(target, artifactctl=artifactctl)
        else:
            self.store = target
        self._telemetryctl = telemetryctl
        self._telemetry_turn_id: str | None = None

    def set_telemetry_context(
        self,
        *,
        session_id: str,
        turn_id: str,
    ) -> None:
        del session_id
        self._telemetry_turn_id = str(turn_id or "").strip() or None

    def _emit_session_operation(
        self,
        *,
        session_id: str,
        operation: str,
        status: str = "ok",
        count: int = 1,
        extra: dict[str, Any] | None = None,
        turn_id: str | None = None,
    ) -> bool:
        resolved_turn_id = str(turn_id or self._telemetry_turn_id or "").strip()
        if not resolved_turn_id:
            return False
        return emit_session_operation(
            telemetryctl=self._telemetryctl,
            session_id=session_id,
            turn_id=resolved_turn_id,
            operation=operation,
            status=status,
            count=count,
            extra=extra,
        )

    @staticmethod
    def _operation_from_event_type(event_type: str) -> str | None:
        normalized = str(event_type or "").strip().lower()
        if normalized.startswith(("tool.", "a2a.", "think.")):
            return "tool_loop"
        if normalized.endswith(".retry") or ".retry." in normalized:
            return "retry"
        return None

    def _ensure_session_exists(self, session_id: str) -> None:
        try:
            current = self.store.get_session(session_id)
        except Exception:
            current = None
        if current is not None:
            return
        self.store.create_session(session_id=session_id)

    def _emit_event_side_effects(
        self,
        *,
        session_id: str,
        turn_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        trace_id: str | None = None,
        actor_type: str | None = None,
        status: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        operation = self._operation_from_event_type(event_type)
        if operation is not None:
            self._emit_session_operation(
                session_id=session_id,
                turn_id=turn_id,
                operation=operation,
                status="error" if error else (status or "ok"),
                extra={"event_type": str(event_type or "").strip()},
            )
        emit_module_telemetry(
            self._telemetryctl,
            "emit_canonical_event",
            session_id,
            turn_id,
            event_type,
            payload,
            trace_id=trace_id,
            actor_type=actor_type,
            status=status,
            error=error,
            logger=_LOG,
        )

    def append_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        attachments: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        self._ensure_session_exists(session_id)
        turn_id = self.store.append_turn(
            session_id=session_id,
            role=role,
            content=content,
            attachments=attachments,
            meta=meta,
        )
        normalized_role = str(role or "").strip().lower()
        if normalized_role == "user":
            self._emit_session_operation(
                session_id=session_id,
                turn_id=turn_id,
                operation="turn_start",
                extra={"role": normalized_role},
            )
        elif normalized_role == "assistant":
            self._emit_session_operation(
                session_id=session_id,
                turn_id=turn_id,
                operation="turn_finish",
                extra={"role": normalized_role},
            )
        emit_module_telemetry(
            self._telemetryctl,
            "emit_canonical_event",
            session_id,
            turn_id,
            f"turn.{normalized_role or 'unknown'}",
            {"role": normalized_role, "content": content},
            logger=_LOG,
        )
        return turn_id

    def append_event(
        self,
        session_id: str,
        type: str,
        payload: dict[str, Any],
        *,
        actor_type: str = "system",
        actor_id: str | None = None,
        trace: dict[str, Any] | None = None,
        refs: dict[str, Any] | None = None,
        parent_event_id: str | None = None,
        importance: int = 1,
        redaction: str | None = None,
        agent_id: str | None = None,
        trace_id: str | None = None,
        task_id: str | None = None,
        parent_id: str | None = None,
        artifact_refs: list[str] | None = None,
        memory_refs: list[str] | None = None,
        status: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> str:
        self._ensure_session_exists(session_id)
        event_id = self.store.append_event(
            session_id=session_id,
            type=type,
            payload=payload,
            actor_type=actor_type,
            actor_id=actor_id,
            trace=trace,
            refs=refs,
            parent_event_id=parent_event_id,
            importance=importance,
            redaction=redaction,
            agent_id=agent_id,
            trace_id=trace_id,
            task_id=task_id,
            parent_id=parent_id,
            artifact_refs=artifact_refs,
            memory_refs=memory_refs,
            status=status,
            error=error,
        )
        self._emit_event_side_effects(
            session_id=session_id,
            turn_id=str(trace_id or event_id),
            event_type=type,
            payload=payload,
            trace_id=trace_id,
            actor_type=actor_type,
            status=status,
            error=error,
        )
        return event_id

    def emit_canonical_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        actor_type: str = "system",
        actor_id: str | None = None,
        trace_id: str | None = None,
        task_id: str | None = None,
        importance: int = 1,
    ) -> str:
        self._ensure_session_exists(session_id)
        event_id = self.store.emit_canonical_event(
            session_id=session_id,
            event_type=event_type,
            payload=payload,
            actor_type=actor_type,
            actor_id=actor_id,
            trace_id=trace_id,
            task_id=task_id,
            importance=importance,
        )
        self._emit_event_side_effects(
            session_id=session_id,
            turn_id=str(trace_id or event_id),
            event_type=event_type,
            payload=payload,
            trace_id=trace_id,
            actor_type=actor_type,
        )
        return event_id

    def put_working_state(
        self,
        session_id: str,
        *,
        state_ref: str | None = None,
        state_inline: dict[str, Any] | None = None,
    ) -> int:
        self._ensure_session_exists(session_id)
        return self.store.put_working_state(
            session_id=session_id,
            state_ref=state_ref,
            state_inline=state_inline,
        )

    def get_latest_working_state(self, session_id: str) -> dict[str, Any] | None:
        return self.store.get_latest_working_state(session_id)

    def update_session_status(self, session_id: str, status: str) -> None:
        self._ensure_session_exists(session_id)
        self.store.update_session_status(session_id, status)

    def list_turns(self, session_id: str) -> list[dict[str, Any]]:
        slice_data = self.store.get_slice(session_id, "chat", {"max_turns": 1000})
        self._emit_session_operation(
            session_id=session_id,
            operation="llm_pack",
            extra={"purpose": "chat", "source": "list_turns"},
        )
        turns = slice_data.get("recent_turns", [])
        return [
            t.model_dump(mode="json") if hasattr(t, "model_dump") else t for t in turns
        ]

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        with self.store._lock:
            rows = self.store._conn.execute(
                "SELECT * FROM session_events WHERE session_id=? ORDER BY seq ASC",
                (session_id,),
            ).fetchall()
            events = [self.store._row_to_session_event(r) for r in rows]
            return [
                e.model_dump(mode="json") if hasattr(e, "model_dump") else e
                for e in events
            ]

    def get_slice(
        self,
        session_id: str,
        purpose: str,
        limits: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self.store.get_slice(session_id, purpose, limits)
        self._emit_session_operation(
            session_id=session_id,
            operation="llm_pack",
            extra={"purpose": str(purpose or "").strip().lower() or "unknown"},
        )
        return payload

    def update_summary(
        self,
        session_id: str,
        summary_short: str,
        *,
        summary_long: str | None = None,
        based_on_seq: int | None = None,
    ) -> None:
        """Update the session summary for continuity across restarts."""
        self._ensure_session_exists(session_id)
        based_seq_value = based_on_seq
        if based_seq_value is None:
            based_seq_value = 0
            latest_event_seq = getattr(self.store, "_latest_event_seq_tx", None)
            lock = getattr(self.store, "_lock", None)
            if callable(latest_event_seq):
                try:
                    if lock is not None:
                        with lock:
                            based_seq_value = int(latest_event_seq(session_id))
                    else:
                        based_seq_value = int(latest_event_seq(session_id))
                except Exception:
                    based_seq_value = 0
        self.store.update_summary(
            session_id=session_id,
            summary_short=summary_short,
            summary_long=summary_long,
            based_on_seq=int(based_seq_value or 0),
        )
