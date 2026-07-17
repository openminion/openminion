from typing import Any, Callable

from openminion.modules.storage.record_store import RecordStore

from .rows import row_to_session_event
from openminion.base.constants import STATE_KEY_WORKING

_KNOWN_CANONICAL_EVENT_TYPES = {
    "llm.request",
    "llm.response",
    "llm.error",
    "llm.call.started",
    "llm.call.completed",
    "llm.call.failed",
    "llm.call.empty_response_accepted",
    "llm.cache.metrics",
    "tool.request",
    "tool.completed",
    "tool.error",
    "job.started",
    "job.completed",
    "job.failed",
    "policy.applied",
    "policy.violation",
    "turn.user",
    "turn.assistant",
    "turn.completed",
    "turn_input.enqueued",
    "turn_input.dequeued",
    "turn_input.dropped",
    "turn_input.moved",
    "turn_input.cancel_requested",
    "turn_input.cancel_acknowledged",
    "turn_input.cancel_failed",
    "turn_input.steer_deferred",
    "decision.made",
    "constraint.set",
    "task.created",
    "task.started",
    "task.completed",
    "task.cancelled",
    "task.failed",
    "task_plan.declared",
    "task_plan.step_completed",
    "task_plan.step_blocked",
    "task_plan.revised",
    "task_plan.abandoned",
    "task_plan.completed",
    "task_plan.invalid_trailer",
    "context.manifest",
    "context.rollover",
    "context.manifest.created",
    "checkpoint.created",
    "seed.created",
    "run.started",
    "run.finished",
    "brain.clarify.requested",
    "brain.clarify.answered",
    "brain.clarify.context_stored",
    "brain.clarify.context_consumed",
    "brain.clarify.context_cleared",
    "brain.assumptions.used",
    "autonomous_turn.fired",
    "brain.idle_tick.started",
    "pae.idle_tick.scheduled",
    "pae.idle_tick.cancelled",
    "pae.idle_tick.suppressed",
    "pae.idle_tick.noop",
    "pae.unsupported_v1_action",
    "response.suppressed",
    "budget.allocated",
    "budget.extended",
    "budget.exhausted",
    "budget.noop_guard",
    "budget.user_declined",
    "budget.user_timeout",
    "budget.high_watermark",
}


class SessionReplayHelper:
    def __init__(
        self,
        *,
        record_store: RecordStore,
        lock: Any,
        get_session: Callable[[str], dict[str, Any] | None],
        get_active_prompt_context: Callable[[str], dict[str, Any] | None],
        get_latest_checkpoint: Callable[[str], dict[str, Any] | None],
        get_latest_seed_bundle: Callable[[str], dict[str, Any] | None],
        latest_event_seq: Callable[[str], int],
        get_latest_working_state: Callable[[str], dict[str, Any] | None],
        append_event: Callable[..., str],
    ) -> None:
        self._record_store = record_store
        self._lock = lock
        self._get_session = get_session
        self._get_active_prompt_context = get_active_prompt_context
        self._get_latest_checkpoint = get_latest_checkpoint
        self._get_latest_seed_bundle = get_latest_seed_bundle
        self._latest_event_seq = latest_event_seq
        self._get_latest_working_state = get_latest_working_state
        self._append_event = append_event

    def enforce_context_manifest(
        self,
        session_id: str,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        warnings: list[str] = []
        prompt_ctx = self._get_active_prompt_context(session_id)

        if prompt_ctx:
            manifest_pc_id = manifest.get("prompt_context_id")
            if manifest_pc_id and manifest_pc_id != prompt_ctx["prompt_context_id"]:
                warnings.append(
                    f"manifest prompt_context_id={manifest_pc_id} != active={prompt_ctx['prompt_context_id']}"
                )

        included_count = len(manifest.get("included_segment_ids", []))
        dropped_count = len(manifest.get("dropped_segment_ids", []))

        result = {
            "session_id": session_id,
            "valid": len(warnings) == 0,
            "warnings": warnings,
            "included_segments": included_count,
            "dropped_segments": dropped_count,
            "prompt_context_id": prompt_ctx["prompt_context_id"]
            if prompt_ctx
            else None,
        }

        try:
            self._append_event(
                session_id=session_id,
                event_type="context.manifest",
                payload=manifest,
                actor_type="system",
            )
        except Exception:
            pass

        return result

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
        payload_value = dict(payload or {})
        if event_type not in _KNOWN_CANONICAL_EVENT_TYPES:
            payload_value.setdefault("_warnings", []).append(
                f"unknown_event_type:{event_type}"
            )

        return self._append_event(
            session_id=session_id,
            event_type=event_type,
            payload=payload_value,
            actor_type=actor_type,
            actor_id=actor_id,
            trace_id=trace_id,
            task_id=task_id,
            importance=importance,
        )

    def get_replay_events(
        self,
        session_id: str,
        *,
        from_seq: int = 0,
        to_seq: int | None = None,
        event_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            query = "SELECT * FROM session_events WHERE session_id=? AND seq >= ?"
            params: list[Any] = [session_id, from_seq]
            if to_seq is not None:
                query += " AND seq <= ?"
                params.append(to_seq)
            query += " ORDER BY seq"
            rows = self._record_store.query_dicts(query, params)
        events = [row_to_session_event(row) for row in rows]
        if event_types:
            events = [event for event in events if event["event_type"] in event_types]
        return events

    def get_resume_state(self, session_id: str) -> dict[str, Any]:
        session = self._get_session(session_id)
        if not session:
            raise ValueError(f"session not found: {session_id}")
        prompt_ctx = self._get_active_prompt_context(session_id)
        latest_cp = self._get_latest_checkpoint(session_id)
        latest_seed = self._get_latest_seed_bundle(session_id)
        latest_seq = self._latest_event_seq(session_id)
        latest_state = self._get_latest_working_state(session_id)
        state_inline = (
            latest_state.get("state_inline")
            if isinstance(latest_state, dict)
            and isinstance(latest_state.get("state_inline"), dict)
            else {}
        )
        unresolved = state_inline.get("unresolved_clarify_items", [])
        clarify_responses = state_inline.get("clarify_responses", {})
        pending_llm_clarify_context = state_inline.get("pending_llm_clarify_context")
        pending_turn_context = state_inline.get("pending_turn_context")
        clarify_events = self.get_replay_events(
            session_id,
            event_types=[
                "brain.clarify.requested",
                "brain.clarify.answered",
                "brain.clarify.context_stored",
                "brain.clarify.context_consumed",
                "brain.clarify.context_cleared",
            ],
        )
        return {
            "session_id": session_id,
            "status": session.get("status", "unknown"),
            "latest_seq": latest_seq,
            "prompt_context": prompt_ctx,
            "latest_checkpoint": latest_cp,
            "latest_seed": latest_seed,
            "active_agent_id": session.get("active_agent_id"),
            STATE_KEY_WORKING: latest_state,
            "resume_keys": {
                "phase": state_inline.get("phase"),
                "cursor": state_inline.get("cursor"),
                "trace_id": state_inline.get("trace_id"),
                "status": state_inline.get("status"),
                "unresolved_clarify_count": len(unresolved)
                if isinstance(unresolved, list)
                else 0,
                "clarify_response_count": len(clarify_responses)
                if isinstance(clarify_responses, dict)
                else 0,
                "pending_llm_clarify_context": bool(
                    isinstance(pending_llm_clarify_context, dict)
                    and pending_llm_clarify_context
                ),
                "pending_turn_context": bool(
                    isinstance(pending_turn_context, dict) and pending_turn_context
                ),
            },
            "clarify_events": [
                {
                    "seq": int(event.get("seq", 0)),
                    "event_type": str(event.get("event_type", "")),
                    "trace_id": str(event.get("trace_id", "")),
                    "event_id": str(event.get("event_id", "")),
                }
                for event in clarify_events
            ],
        }

    def backfill_events(
        self,
        session_id: str,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        imported = 0
        skipped = 0
        for event in events:
            event_type = event.get("event_type") or event.get("type")
            if not event_type:
                skipped += 1
                continue
            payload = event.get("payload", {})
            try:
                self._append_event(
                    session_id=session_id,
                    event_type=event_type,
                    payload=payload,
                    actor_type=event.get("actor_type", "backfill"),
                    actor_id=event.get("actor_id"),
                    trace_id=event.get("trace_id"),
                    task_id=event.get("task_id"),
                    importance=event.get("importance", 1),
                )
                imported += 1
            except Exception:
                skipped += 1

        return {
            "session_id": session_id,
            "imported": imported,
            "skipped": skipped,
            "total": len(events),
        }
