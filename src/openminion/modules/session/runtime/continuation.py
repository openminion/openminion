"""Explicit, bounded continuation between sessions in one local store."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import sqlite3
from threading import Lock, RLock
from time import perf_counter
from typing import Any, Literal

from sqlalchemy.exc import IntegrityError as SQLAlchemyIntegrityError

from openminion.base.constants import STATE_KEY_WORKING
from ..interfaces import SESSION_CONTINUATION_SCHEMA_VERSION
from ..schemas import (
    ContinuationApplyResult,
    ContinuationBuildResult,
    ContinuationError,
    ContinuationPreview,
    ContinuationProgressItem,
    DEFAULT_CONTINUATION_TTL_SECONDS,
    MAX_CONTINUATION_TTL_SECONDS,
    SessionContinuationPacket,
    SessionContinuationPayload,
)

PACKET_CREATED = "session.continuation.packet_created"
PACKET_APPLIED = "session.continuation.applied"
PACKET_REJECTED = "session.continuation.rejected"
PACKET_EXPIRED = "session.continuation.expired"

_STORE_LOCKS: dict[str, RLock] = {}
_STORE_LOCKS_GUARD = Lock()


def _utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1_000)


def _store_lock(store: Any) -> RLock:
    key = str(getattr(store, "database_path", id(store)))
    with _STORE_LOCKS_GUARD:
        return _STORE_LOCKS.setdefault(key, RLock())


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _strings(value: Any, *, limit: int = 48) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for raw in value:
        item = str(raw or "").strip()
        if item and item not in result:
            result.append(item)
        if len(result) >= limit:
            break
    return result


def _item_id(value: Any) -> str:
    item = _dict(value)
    for key in ("step_id", "intent_id", "id", "clarification_id"):
        candidate = str(item.get(key) or "").strip()
        if candidate:
            return candidate
    return ""


def _progress_items(value: Any) -> list[ContinuationProgressItem]:
    if not isinstance(value, list):
        return []
    result: list[ContinuationProgressItem] = []
    for raw in value[:24]:
        item = _dict(raw)
        item_id = _item_id(item)
        status = str(item.get("status") or "unknown").strip()
        if item_id:
            result.append(ContinuationProgressItem(item_id=item_id, status=status))
    return result


def _state_from_resume(resume: dict[str, Any]) -> dict[str, Any]:
    working = _dict(resume.get(STATE_KEY_WORKING))
    return _dict(working.get("state_inline"))


class SessionContinuationService:
    """Session-owned preview, create, lookup, and apply implementation."""

    def __init__(
        self,
        store: Any,
        *,
        now_ms: Callable[[], int] = _utc_now_ms,
        telemetry_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._store = store
        self._now_ms = now_ms
        self._telemetry_sink = telemetry_sink
        self._lock = _store_lock(store)

    def preview(
        self,
        source_session_id: str,
        *,
        target_agent_id: str,
        expires_in_seconds: int = DEFAULT_CONTINUATION_TTL_SECONDS,
    ) -> ContinuationPreview:
        started = perf_counter()
        source = self._store.get_session(source_session_id)
        if source is None:
            raise ContinuationError("continuation_source_not_found")
        source_agent_id, target_agent, ttl = _preview_inputs(
            source,
            target_agent_id=target_agent_id,
            expires_in_seconds=expires_in_seconds,
        )

        resume = self._store.get_resume_state(source_session_id)
        state = _state_from_resume(resume)
        plan = _dict(self._store.get_active_task_plan(source_session_id))
        latest_seq = int(resume.get("latest_seq") or 0)
        now = int(self._now_ms())
        checkpoint = _dict(resume.get("latest_checkpoint"))
        checkpoint_ref = str(
            checkpoint.get("checkpoint_id") or checkpoint.get("id") or ""
        ).strip()
        recent_tool_events = self._store.get_recent_tool_events(source_session_id, 24)
        recent_event_refs, artifact_refs = _recent_refs(recent_tool_events)

        unresolved_ids, omitted = _unresolved_ids_and_omissions(state)
        summary = _session_work_summary(state)

        meta = _dict(source.get("meta"))
        payload = SessionContinuationPayload(
            created_at_ms=now,
            expires_at_ms=now + ttl * 1_000,
            source_session_id=source_session_id,
            source_latest_seq=latest_seq,
            source_agent_id=source_agent_id,
            target_agent_id=target_agent,
            source_checkpoint_ref=checkpoint_ref or None,
            workspace_ref=_optional_ref(meta, state, "workspace_ref"),
            project_run_ref=_optional_ref(meta, state, "project_run_ref"),
            task_ref=_optional_ref(meta, state, "task_ref", "task_id"),
            goal_ref=_optional_ref(meta, state, "goal_ref", "active_goal_id"),
            phase=_optional_text(state.get("phase")),
            cursor=_optional_nonnegative_int(state.get("cursor")),
            termination_reason=_optional_text(state.get("termination_reason")),
            plan_steps=_progress_items(plan.get("steps")),
            intents=_progress_items(state.get("intent_execution_states")),
            unresolved_clarification_ids=unresolved_ids,
            pending_input_refs=_strings(state.get("pending_input_refs")),
            session_work_summary=summary,
            session_work_summary_ref=_optional_text(
                state.get("session_work_summary_ref")
            ),
            memory_refs=_strings(state.get("decision_memory_refs")),
            artifact_refs=artifact_refs,
            checkpoint_refs=[checkpoint_ref] if checkpoint_ref else [],
            project_refs=_strings(state.get("project_refs")),
            recent_event_refs=recent_event_refs,
            permission_refs=_strings(state.get("permission_refs")),
            omitted_field_reasons=omitted,
            redaction_summary={"omitted_fields": len(omitted)},
            owner_versions={
                "session": SESSION_CONTINUATION_SCHEMA_VERSION,
                "brain_state": str(working_version(resume)),
            },
        )
        warnings = list(omitted)
        if payload.permission_refs:
            warnings.append("permission_revalidation_required")
        self._emit(
            "session.continuation.build",
            started,
            status="previewed",
            ref_count=_ref_count(payload),
            redaction_count=len(omitted),
        )
        return ContinuationPreview(payload=payload, warnings=warnings)

    def create(
        self,
        source_session_id: str,
        *,
        target_agent_id: str,
        expires_in_seconds: int = DEFAULT_CONTINUATION_TTL_SECONDS,
    ) -> ContinuationBuildResult:
        preview = self.preview(
            source_session_id,
            target_agent_id=target_agent_id,
            expires_in_seconds=expires_in_seconds,
        )
        event_id = self._store.append_event(
            source_session_id,
            event_type=PACKET_CREATED,
            payload=preview.payload.model_dump(mode="json"),
            refs={"source_session_id": source_session_id},
            importance=2,
            redaction="bounded",
        )
        packet = self.get_packet(event_id)
        return ContinuationBuildResult(
            status="created",
            preview=preview,
            packet=packet,
        )

    def get_packet(self, packet_id: str) -> SessionContinuationPacket:
        event = self._store.get_event_by_id(packet_id)
        if event is None:
            raise ContinuationError("continuation_packet_not_found")
        if event.get("event_type") != PACKET_CREATED:
            raise ContinuationError("continuation_packet_wrong_event_type")
        try:
            payload = SessionContinuationPayload.model_validate(event.get("payload"))
            return SessionContinuationPacket(
                packet_id=str(event["event_id"]),
                event_seq=int(event["seq"]),
                event_timestamp=str(event["timestamp"]),
                source_session_id=str(event["session_id"]),
                payload=payload,
            )
        except ValueError as exc:
            code = (
                "unsupported_continuation_schema"
                if ("unsupported_continuation_schema" in str(exc))
                else "invalid_continuation_packet"
            )
            raise ContinuationError(code) from exc

    def apply(
        self,
        target_session_id: str,
        *,
        packet_id: str,
    ) -> ContinuationApplyResult:
        started = perf_counter()
        packet = self.get_packet(packet_id)
        with self._lock:
            prior = self._store.get_events_by_parent_and_type(packet_id, PACKET_APPLIED)
            if prior:
                applied = prior[0]
                prior_target = str(applied.get("session_id") or "")
                if prior_target == target_session_id:
                    return self._apply_result(
                        packet,
                        target_session_id,
                        target_event_id=str(applied.get("event_id") or ""),
                        status="already_applied",
                    )
                return self._reject(
                    packet,
                    target_session_id,
                    "continuation_target_conflict",
                    started=started,
                )

            reason = self._eligibility_failure(packet, target_session_id)
            if reason:
                return self._reject(
                    packet,
                    target_session_id,
                    reason,
                    started=started,
                )

            try:
                event_id = self._append_applied_event(packet, target_session_id)
            except (sqlite3.IntegrityError, SQLAlchemyIntegrityError):
                # The unique lineage index is the cross-process authority. If
                # another writer won the race, translate that durable result
                # into the same idempotent/conflict contract as the fast path.
                prior = self._store.get_events_by_parent_and_type(
                    packet.packet_id,
                    PACKET_APPLIED,
                )
                if not prior:
                    raise
                applied = prior[0]
                prior_target = str(applied.get("session_id") or "")
                if prior_target == target_session_id:
                    return self._apply_result(
                        packet,
                        target_session_id,
                        target_event_id=str(applied.get("event_id") or ""),
                        status="already_applied",
                    )
                return self._reject(
                    packet,
                    target_session_id,
                    "continuation_target_conflict",
                    started=started,
                )
        self._emit(
            "session.continuation.apply",
            started,
            status="applied",
            ref_count=_ref_count(packet.payload),
            pinned_token_count=max(1, len(packet.payload.session_work_summary) // 4),
        )
        return self._apply_result(
            packet,
            target_session_id,
            target_event_id=event_id,
            status="applied",
        )

    def _append_applied_event(
        self,
        packet: SessionContinuationPacket,
        target_session_id: str,
    ) -> str:
        return str(
            self._store.append_event(
                target_session_id,
                event_type=PACKET_APPLIED,
                parent_event_id=packet.packet_id,
                payload={
                    "packet_id": packet.packet_id,
                    "source_session_id": packet.source_session_id,
                    "schema_version": packet.payload.schema_version,
                    "continuation": packet.payload.model_dump(mode="json"),
                    "permission_revalidation_required": bool(
                        packet.payload.permission_refs
                    ),
                },
                refs={
                    "source_event_id": packet.packet_id,
                    "source_session_id": packet.source_session_id,
                },
                importance=2,
                redaction="bounded",
            )
        )

    def _eligibility_failure(
        self,
        packet: SessionContinuationPacket,
        target_session_id: str,
    ) -> str | None:
        target = self._store.get_session(target_session_id)
        if target is None:
            return "continuation_target_not_found"
        if self._now_ms() >= packet.payload.expires_at_ms:
            return "continuation_expired"
        target_agent = str(target.get("active_agent_id") or "").strip()
        if target_agent != packet.payload.target_agent_id:
            return "continuation_agent_mismatch"
        if self._store.get_total_turn_count(target_session_id) != 0:
            return "continuation_target_not_empty"
        if self._store.get_events(target_session_id, types=[PACKET_APPLIED]):
            return "continuation_target_already_initialized"
        target_meta = _dict(target.get("meta"))
        for field in ("workspace_ref", "project_run_ref"):
            expected = getattr(packet.payload, field)
            actual = _optional_ref(target_meta, {}, field)
            if expected and actual != expected:
                return f"continuation_{field.removesuffix('_ref')}_mismatch"
        return None

    def _reject(
        self,
        packet: SessionContinuationPacket,
        target_session_id: str,
        reason: str,
        *,
        started: float,
    ) -> ContinuationApplyResult:
        event_type = (
            PACKET_EXPIRED if reason == "continuation_expired" else PACKET_REJECTED
        )
        self._store.append_event(
            packet.source_session_id,
            event_type=event_type,
            parent_event_id=packet.packet_id,
            payload={
                "packet_id": packet.packet_id,
                "target_session_id": target_session_id,
                "reason_code": reason,
            },
            importance=2,
            redaction="bounded",
        )
        self._emit(
            "session.continuation.apply",
            started,
            status="rejected",
            rejection_reason=reason,
        )
        return self._apply_result(
            packet,
            target_session_id,
            target_event_id=None,
            status="rejected",
            reason_code=reason,
        )

    @staticmethod
    def _apply_result(
        packet: SessionContinuationPacket,
        target_session_id: str,
        *,
        target_event_id: str | None,
        status: Literal["applied", "already_applied", "rejected"],
        reason_code: str | None = None,
    ) -> ContinuationApplyResult:
        return ContinuationApplyResult(
            status=status,
            packet_id=packet.packet_id,
            source_session_id=packet.source_session_id,
            target_session_id=target_session_id,
            source_event_id=packet.packet_id,
            target_event_id=target_event_id,
            reason_code=reason_code,
            warnings=["permission_revalidation_required"]
            if packet.payload.permission_refs
            else [],
        )

    def _emit(self, event_type: str, started: float, **payload: Any) -> None:
        if self._telemetry_sink is None:
            return
        try:
            self._telemetry_sink(
                event_type,
                {
                    "duration_ms": max(0, round((perf_counter() - started) * 1_000)),
                    **payload,
                },
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            return


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_ref(
    meta: dict[str, Any], state: dict[str, Any], *keys: str
) -> str | None:
    for key in keys:
        value = _optional_text(state.get(key) or meta.get(key))
        if value:
            return value
    return None


def _preview_inputs(
    source: dict[str, Any],
    *,
    target_agent_id: str,
    expires_in_seconds: int,
) -> tuple[str, str, int]:
    source_agent_id = str(source.get("active_agent_id") or "").strip()
    target_agent = str(target_agent_id or "").strip()
    if not source_agent_id:
        raise ContinuationError("continuation_source_agent_missing")
    if not target_agent:
        raise ContinuationError("continuation_target_agent_required")
    ttl = int(expires_in_seconds)
    if ttl <= 0 or ttl > MAX_CONTINUATION_TTL_SECONDS:
        raise ContinuationError("invalid_continuation_expiry")
    return source_agent_id, target_agent, ttl


def _recent_refs(
    recent_tool_events: list[Any],
) -> tuple[list[str], list[str]]:
    recent_event_refs = [
        str(item.get("event_id") or "").strip()
        for item in recent_tool_events
        if isinstance(item, dict) and item.get("event_id")
    ]
    artifact_refs: list[str] = []
    for item in recent_tool_events:
        if isinstance(item, dict):
            artifact_refs.extend(_strings(item.get("artifact_refs")))
    return recent_event_refs, artifact_refs


def _unresolved_ids_and_omissions(
    state: dict[str, Any],
) -> tuple[list[str], list[str]]:
    unresolved = state.get("unresolved_clarify_items")
    unresolved_ids = [
        item_id
        for item_id in (_item_id(item) for item in (unresolved or []))
        if item_id
    ]
    omitted: list[str] = []
    if unresolved and not unresolved_ids:
        omitted.append("unresolved_clarifications_missing_stable_ids")
    if state.get("pending_turn_context") and not state.get("pending_input_refs"):
        omitted.append("pending_turn_context_has_no_durable_ref")
    return unresolved_ids, omitted


def _session_work_summary(state: dict[str, Any]) -> str:
    summary_value = state.get("session_work_summary")
    if isinstance(summary_value, dict):
        return str(summary_value.get("summary") or "")
    return str(summary_value or "")


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def working_version(resume: dict[str, Any]) -> str:
    working = _dict(resume.get(STATE_KEY_WORKING))
    return str(working.get("version") or "unknown")


def _ref_count(payload: SessionContinuationPayload) -> int:
    return sum(
        len(values)
        for values in (
            payload.memory_refs,
            payload.artifact_refs,
            payload.checkpoint_refs,
            payload.project_refs,
            payload.recent_event_refs,
            payload.permission_refs,
        )
    )


__all__ = [
    "PACKET_APPLIED",
    "PACKET_CREATED",
    "PACKET_EXPIRED",
    "PACKET_REJECTED",
    "SessionContinuationService",
]
