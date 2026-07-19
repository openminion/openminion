from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Mapping, Optional

from openminion.modules.controlplane.runtime.client import RunStatus as RunStatus
from openminion.modules.storage.runtime.session_store import EventRecord, SessionStore
from .constants import RUN_STATUS_DEFAULT_SCAN_LIMIT

RUN_STATE_QUEUED = "queued"
RUN_STATE_RUNNING = "running"
RUN_STATE_WAITING_TOOL = "waiting_tool"
RUN_STATE_RESPONDING = "responding"
RUN_STATE_COMPLETED = "completed"
RUN_STATE_FAILED = "failed"
RUN_STATE_CANCELLED = "cancelled"

_RUN_STATES = {
    RUN_STATE_QUEUED,
    RUN_STATE_RUNNING,
    RUN_STATE_WAITING_TOOL,
    RUN_STATE_RESPONDING,
    RUN_STATE_COMPLETED,
    RUN_STATE_FAILED,
    RUN_STATE_CANCELLED,
}
_TERMINAL_RUN_STATES = {
    RUN_STATE_COMPLETED,
    RUN_STATE_FAILED,
    RUN_STATE_CANCELLED,
}

THREAD_STATE_AWAITING = "awaiting_response"
THREAD_STATE_RESPONSE_UNDELIVERED = "response_ready_undelivered"
THREAD_STATE_SETTLED = "settled"
THREAD_STATE_FAILED = "failed"
THREAD_STATE_CANCELLED = "cancelled"
THREAD_STATE_DETACHED = "detached"

DELIVERY_STATE_NONE = "none"
DELIVERY_STATE_PERSISTED = "persisted"
DELIVERY_STATE_DELIVERED = "delivered"
DELIVERY_STATE_ACKED = "acked"

ATTACH_ROLE_WRITER = "writer"
ATTACH_ROLE_OBSERVER = "observer"

THREAD_DECISION_REPLAY = "replay"
THREAD_DECISION_RESUME = "resume_thread"
THREAD_DECISION_FORK = "fork_thread"
THREAD_DECISION_REJECT = "reject"


RunTerminalState = Literal[
    "completed",
    "failed",
    "blocked",
    "needs_human",
    "budget_exhausted",
]

RUN_TERMINAL_COMPLETED: RunTerminalState = "completed"
RUN_TERMINAL_FAILED: RunTerminalState = "failed"
RUN_TERMINAL_BLOCKED: RunTerminalState = "blocked"
RUN_TERMINAL_NEEDS_HUMAN: RunTerminalState = "needs_human"
RUN_TERMINAL_BUDGET_EXHAUSTED: RunTerminalState = "budget_exhausted"

# TGCR-Q5: canonical event-type identifier for `RunCheckpoint` payloads
RUN_CHECKPOINT_EVENT_TYPE = "run.checkpoint"

_RUN_TERMINAL_STATES: frozenset[str] = frozenset(
    {
        RUN_TERMINAL_COMPLETED,
        RUN_TERMINAL_FAILED,
        RUN_TERMINAL_BLOCKED,
        RUN_TERMINAL_NEEDS_HUMAN,
        RUN_TERMINAL_BUDGET_EXHAUSTED,
    }
)


def is_run_terminal_state(value: Any) -> bool:
    """Return True if ``value`` names a structurally terminal run state.

    Pure function. Used by consumers (MTRC, AMEC, AMEB Phase 2) that
    need to test terminal-state strings without importing the Literal.
    """

    return isinstance(value, str) and value in _RUN_TERMINAL_STATES


def resolve_run_terminal_persistence(terminal: str) -> str:
    """Map a typed ``RunTerminalState`` value to the persisted"""

    if not is_run_terminal_state(terminal):
        raise ValueError(
            f"Unknown RunTerminalState: {terminal!r}. "
            f"Allowed: {sorted(_RUN_TERMINAL_STATES)}"
        )
    if terminal == RUN_TERMINAL_COMPLETED:
        return RUN_STATE_COMPLETED
    return RUN_STATE_FAILED


@dataclass(frozen=True)
class RunCheckpoint:
    """Typed replayable checkpoint record for a typed ``Run``."""

    checkpoint_id: str
    run_id: str
    goal_id: str
    sequence: int
    state_snapshot: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "run_id": self.run_id,
            "goal_id": self.goal_id,
            "sequence": self.sequence,
            "state_snapshot": dict(self.state_snapshot),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class Run:
    """Run contract."""

    run_id: str
    session_id: str
    goal_id: str
    state: str  # one of RUN_STATE_*
    terminal_state: Optional[str] = None  # one of RUN_TERMINAL_* when terminal
    apd_plan_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "goal_id": self.goal_id,
            "state": self.state,
        }
        if self.terminal_state is not None:
            payload["terminal_state"] = self.terminal_state
        if self.apd_plan_id is not None:
            payload["apd_plan_id"] = self.apd_plan_id
        return payload

    def is_terminal(self) -> bool:
        return self.state in _TERMINAL_RUN_STATES


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    session_id: str
    state: str
    current_step: str
    started_at: str
    ended_at: str
    event_count: int
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "state": self.state,
            "current_step": self.current_step,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "event_count": self.event_count,
        }
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True)
class RunEvent:
    id: int
    run_id: str
    session_id: str
    event_type: str
    state: str
    current_step: str
    payload: Dict[str, Any]
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "event_type": self.event_type,
            "state": self.state,
            "current_step": self.current_step,
            "payload": self.payload,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ThreadLifecycleProjection:
    conversation_id: str
    thread_id: str
    thread_state: str
    delivery_state: str
    writer_attach_id: str
    latest_run_id: str
    latest_run_state: str
    latest_event_id: int
    latest_message_id: str
    latest_message_role: str
    qualifier: str
    pending_response_id: str
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "thread_id": self.thread_id,
            "thread_state": self.thread_state,
            "delivery_state": self.delivery_state,
            "writer_attach_id": self.writer_attach_id,
            "latest_run_id": self.latest_run_id,
            "latest_run_state": self.latest_run_state,
            "latest_event_id": self.latest_event_id,
            "latest_message_id": self.latest_message_id,
            "latest_message_role": self.latest_message_role,
            "qualifier": self.qualifier,
            "pending_response_id": self.pending_response_id,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ThreadRoutingDecision:
    action: str
    reason_code: str
    thread_id: str
    should_replay_pending: bool

    def to_dict(self) -> Dict[str, str]:
        return {
            "action": self.action,
            "reason_code": self.reason_code,
            "thread_id": self.thread_id,
            "should_replay_pending": str(self.should_replay_pending).lower(),
        }


def append_run_state_event(
    sessions: SessionStore,
    *,
    session_id: str,
    run_id: str,
    state: str,
    current_step: str,
    payload: Optional[Mapping[str, Any]] = None,
    conversation_id: str | None = None,
    thread_id: str | None = None,
    attach_id: str | None = None,
    session_turn_fence_token: int | None = None,
) -> EventRecord:
    normalized_run_id = str(run_id).strip()
    if not normalized_run_id:
        raise ValueError("`run_id` is required.")

    normalized_state = _normalize_run_state(state=state, source_event_type="")
    normalized_step = str(current_step).strip()
    event_payload: Dict[str, Any] = {
        "run_id": normalized_run_id,
        "state": normalized_state,
        "step": normalized_step,
    }
    conversation_value = str(conversation_id or "").strip()
    thread_value = str(thread_id or "").strip()
    attach_value = str(attach_id or "").strip()
    if conversation_value:
        event_payload["conversation_id"] = conversation_value
    if thread_value:
        event_payload["thread_id"] = thread_value
    if attach_value:
        event_payload["attach_id"] = attach_value
    if payload:
        event_payload.update(dict(payload))

    event_kwargs: Dict[str, Any] = {
        "session_id": session_id,
        "event_type": f"run.{normalized_state}",
        "payload": event_payload,
    }
    if session_turn_fence_token is not None:
        event_kwargs["session_turn_fence_token"] = session_turn_fence_token
    return sessions.append_event(**event_kwargs)


def append_run_checkpoint_event(
    sessions: SessionStore,
    *,
    session_id: str,
    checkpoint: RunCheckpoint,
    conversation_id: str | None = None,
    thread_id: str | None = None,
) -> EventRecord:
    """Persist a typed ``RunCheckpoint`` via the existing event log."""

    if not checkpoint.run_id:
        raise ValueError("RunCheckpoint.run_id is required")
    if not checkpoint.goal_id:
        raise ValueError("RunCheckpoint.goal_id is required")
    if not checkpoint.checkpoint_id:
        raise ValueError("RunCheckpoint.checkpoint_id is required")

    payload: Dict[str, Any] = checkpoint.to_dict()
    conversation_value = str(conversation_id or "").strip()
    thread_value = str(thread_id or "").strip()
    if conversation_value:
        payload["conversation_id"] = conversation_value
    if thread_value:
        payload["thread_id"] = thread_value

    return sessions.append_event(
        session_id=session_id,
        event_type=RUN_CHECKPOINT_EVENT_TYPE,
        payload=payload,
    )


def append_lifecycle_event(
    sessions: SessionStore,
    *,
    session_id: str,
    event_type: str,
    conversation_id: str | None = None,
    thread_id: str | None = None,
    attach_id: str | None = None,
    payload: Optional[Mapping[str, Any]] = None,
    session_turn_fence_token: int | None = None,
) -> EventRecord:
    normalized_event = str(event_type or "").strip()
    if not normalized_event:
        raise ValueError("`event_type` is required.")
    event_payload: Dict[str, Any] = {}
    conversation_value = str(conversation_id or "").strip()
    thread_value = str(thread_id or "").strip()
    attach_value = str(attach_id or "").strip()
    if conversation_value:
        event_payload["conversation_id"] = conversation_value
    if thread_value:
        event_payload["thread_id"] = thread_value
    if attach_value:
        event_payload["attach_id"] = attach_value
    if payload:
        event_payload.update(dict(payload))
    event_kwargs: Dict[str, Any] = {
        "session_id": session_id,
        "event_type": normalized_event,
        "payload": event_payload,
    }
    if session_turn_fence_token is not None:
        event_kwargs["session_turn_fence_token"] = session_turn_fence_token
    return sessions.append_event(**event_kwargs)


def list_session_runs(
    sessions: SessionStore,
    *,
    session_id: str,
    limit: int = 20,
    scan_limit: int = RUN_STATUS_DEFAULT_SCAN_LIMIT,
) -> List[RunSummary]:
    safe_limit = max(1, min(int(limit), 500))
    safe_scan_limit = max(safe_limit, int(scan_limit))
    events = sessions.list_events(
        session_id=session_id,
        limit=safe_scan_limit,
        event_type_prefix="run.",
        newest_first=False,
    )

    summaries: Dict[str, Dict[str, Any]] = {}
    for event in events:
        run_event = _to_run_event(event)
        if run_event is None:
            continue

        summary = summaries.get(run_event.run_id)
        if summary is None:
            summary = {
                "run_id": run_event.run_id,
                "session_id": run_event.session_id,
                "state": run_event.state,
                "current_step": run_event.current_step,
                "started_at": run_event.created_at,
                "ended_at": "",
                "event_count": 0,
                "error": "",
            }
            summaries[run_event.run_id] = summary

        summary["event_count"] += 1
        summary["state"] = run_event.state
        if run_event.current_step:
            summary["current_step"] = run_event.current_step
        if run_event.state in _TERMINAL_RUN_STATES:
            summary["ended_at"] = run_event.created_at
        run_error = _extract_event_error(run_event.payload)
        if run_error:
            summary["error"] = run_error

    ordered = sorted(
        summaries.values(),
        key=lambda item: (item["started_at"], item["run_id"]),
        reverse=True,
    )
    return [
        RunSummary(
            run_id=str(item["run_id"]),
            session_id=str(item["session_id"]),
            state=str(item["state"]),
            current_step=str(item["current_step"]),
            started_at=str(item["started_at"]),
            ended_at=str(item["ended_at"]),
            event_count=int(item["event_count"]),
            error=str(item["error"]),
        )
        for item in ordered[:safe_limit]
    ]


def list_session_run_events(
    sessions: SessionStore,
    *,
    session_id: str,
    run_id: str,
    limit: int = 200,
    scan_limit: int = RUN_STATUS_DEFAULT_SCAN_LIMIT,
) -> List[RunEvent]:
    normalized_run_id = str(run_id).strip()
    if not normalized_run_id:
        return []

    safe_limit = max(1, min(int(limit), 1000))
    safe_scan_limit = max(safe_limit, int(scan_limit))
    events = sessions.list_events(
        session_id=session_id,
        limit=safe_scan_limit,
        event_type_prefix="run.",
        newest_first=False,
    )
    run_events = [
        item
        for item in (_to_run_event(event) for event in events)
        if item is not None and item.run_id == normalized_run_id
    ]
    if len(run_events) <= safe_limit:
        return run_events
    return run_events[-safe_limit:]


def _to_run_event(event: EventRecord) -> Optional[RunEvent]:
    run_id = str(event.payload.get("run_id", "")).strip()
    if not run_id:
        return None

    state = _normalize_run_state(
        state=event.payload.get("state"), source_event_type=event.event_type
    )
    current_step = str(event.payload.get("step", "")).strip()
    payload = dict(event.payload)
    return RunEvent(
        id=int(event.id),
        run_id=run_id,
        session_id=event.session_id,
        event_type=event.event_type,
        state=state,
        current_step=current_step,
        payload=payload,
        created_at=event.created_at,
    )


def _normalize_run_state(*, state: Any, source_event_type: str) -> str:
    candidate = str(state or "").strip().lower()
    if candidate in _RUN_STATES:
        return candidate

    if source_event_type.startswith("run."):
        suffix = source_event_type.split(".", 1)[1].strip().lower()
        if suffix in _RUN_STATES:
            return suffix
    return RUN_STATE_RUNNING


def _extract_event_error(payload: Mapping[str, Any]) -> str:
    raw_error = payload.get("error")
    if raw_error is None:
        return ""
    return str(raw_error).strip()


def resolve_thread_routing_decision(
    *,
    lifecycle: ThreadLifecycleProjection,
    session_id: str,
    conversation_id: str,
    requested_thread_id: str,
    attach_id: str,
    resume_requested: bool,
    reset_requested: bool,
    explicit_thread: bool,
    auto_resume_inferred: bool,
) -> ThreadRoutingDecision:
    conversation_value = str(conversation_id or "").strip()
    requested_thread = str(requested_thread_id or "").strip()
    attach_value = str(attach_id or "").strip()
    current_thread = requested_thread or str(lifecycle.thread_id or "").strip()
    if not current_thread:
        current_thread = conversation_value or str(session_id or "").strip()
    if not current_thread:
        current_thread = "thread"

    if reset_requested:
        return ThreadRoutingDecision(
            action=THREAD_DECISION_FORK,
            reason_code="reset_requested",
            thread_id=_new_thread_id_for_decision(conversation_value or session_id),
            should_replay_pending=False,
        )

    if lifecycle.thread_state == THREAD_STATE_RESPONSE_UNDELIVERED:
        return ThreadRoutingDecision(
            action=THREAD_DECISION_REPLAY,
            reason_code="undelivered_response_pending",
            thread_id=current_thread,
            should_replay_pending=True,
        )

    if lifecycle.thread_state == THREAD_STATE_SETTLED and not resume_requested:
        if not attach_value or attach_value != lifecycle.writer_attach_id:
            return ThreadRoutingDecision(
                action=THREAD_DECISION_FORK,
                reason_code="settled_without_resume",
                thread_id=_new_thread_id_for_decision(conversation_value or session_id),
                should_replay_pending=False,
            )
        return ThreadRoutingDecision(
            action=THREAD_DECISION_RESUME,
            reason_code="writer_attach_match",
            thread_id=current_thread,
            should_replay_pending=False,
        )

    if explicit_thread:
        reason_code = "explicit_thread_requested"
    elif auto_resume_inferred:
        reason_code = "implicit_resume_without_explicit_session"
    elif resume_requested:
        reason_code = "resume_requested"
    else:
        reason_code = f"lifecycle_{lifecycle.thread_state}"
    return ThreadRoutingDecision(
        action=THREAD_DECISION_RESUME,
        reason_code=reason_code,
        thread_id=current_thread,
        should_replay_pending=False,
    )


def resolve_thread_lifecycle(
    sessions: SessionStore,
    *,
    session_id: str,
    conversation_id: str | None = None,
    thread_id: str | None = None,
    scan_limit: int = RUN_STATUS_DEFAULT_SCAN_LIMIT,
) -> ThreadLifecycleProjection:
    conversation_value = str(conversation_id or "").strip()
    thread_value = str(thread_id or "").strip()

    recent_messages = sessions.list_recent_messages(
        session_id=session_id,
        limit=50,
        conversation_id=conversation_value or None,
        thread_id=thread_value or None,
    )
    inferred_thread = _infer_thread_id(
        messages=recent_messages,
        conversation_id=conversation_value,
        session_id=session_id,
    )
    if not thread_value:
        thread_value = inferred_thread

    events = sessions.list_events(
        session_id=session_id,
        limit=max(100, int(scan_limit)),
        newest_first=False,
    )
    filtered_events = [
        event
        for event in events
        if _event_matches_thread(
            event,
            conversation_id=conversation_value,
            thread_id=thread_value,
            session_id=session_id,
        )
    ]

    run_events = [
        _to_run_event(event)
        for event in filtered_events
        if event.event_type.startswith("run.")
    ]
    run_events = [event for event in run_events if event is not None]
    latest_run_event = run_events[-1] if run_events else None

    delivery_state = _resolve_delivery_state(filtered_events)
    attach_id = _resolve_writer_attach_id(
        filtered_events=filtered_events,
        messages=recent_messages,
    )
    last_message = recent_messages[-1] if recent_messages else None
    latest_message_role = str(last_message.role) if last_message is not None else ""
    latest_message_id = str(last_message.id) if last_message is not None else ""

    thread_state, qualifier, pending_response_id = _resolve_thread_state(
        latest_run_event=latest_run_event,
        delivery_state=delivery_state,
        latest_message=last_message,
        filtered_events=filtered_events,
    )

    updated_at = _latest_event_time(filtered_events) or (
        last_message.created_at if last_message is not None else ""
    )
    latest_event_id = int(filtered_events[-1].id) if filtered_events else 0

    return ThreadLifecycleProjection(
        conversation_id=conversation_value,
        thread_id=thread_value,
        thread_state=thread_state,
        delivery_state=delivery_state,
        writer_attach_id=attach_id,
        latest_run_id=latest_run_event.run_id if latest_run_event else "",
        latest_run_state=latest_run_event.state if latest_run_event else "",
        latest_event_id=latest_event_id,
        latest_message_id=latest_message_id,
        latest_message_role=latest_message_role,
        qualifier=qualifier,
        pending_response_id=pending_response_id,
        updated_at=updated_at,
    )


def _infer_thread_id(
    *,
    messages: List[Any],
    conversation_id: str,
    session_id: str,
) -> str:
    if messages:
        meta = getattr(messages[-1], "metadata", {}) or {}
        candidate = str(meta.get("thread_id", "")).strip()
        if not candidate:
            candidate = str(getattr(messages[-1], "thread_id", "") or "").strip()
        if candidate:
            return candidate
    if conversation_id:
        return conversation_id
    return str(session_id)


def _event_matches_thread(
    event: EventRecord,
    *,
    conversation_id: str,
    thread_id: str,
    session_id: str,
) -> bool:
    payload = event.payload or {}
    event_conversation = str(payload.get("conversation_id", "") or "").strip()
    event_thread = str(payload.get("thread_id", "") or "").strip()
    if conversation_id:
        if event_conversation:
            if event_conversation != conversation_id:
                return False
        elif conversation_id != session_id:
            return False
    if thread_id and event_thread and event_thread != thread_id:
        return False
    return True


def _resolve_delivery_state(events: List[EventRecord]) -> str:
    delivery_state = DELIVERY_STATE_NONE
    for event in events:
        event_type = str(event.event_type)
        if event_type == "response.acked":
            delivery_state = DELIVERY_STATE_ACKED
        elif event_type == "response.delivered":
            if delivery_state != DELIVERY_STATE_ACKED:
                delivery_state = DELIVERY_STATE_DELIVERED
        elif event_type == "response.persisted":
            if delivery_state == DELIVERY_STATE_NONE:
                delivery_state = DELIVERY_STATE_PERSISTED
    return delivery_state


def _message_metadata(message: Any) -> Mapping[str, Any]:
    metadata = getattr(message, "metadata", {}) or {}
    return metadata if isinstance(metadata, Mapping) else {}


def _is_internal_error_outbound(message: Any) -> bool:
    if message is None or str(getattr(message, "role", "") or "").strip() != "outbound":
        return False
    metadata = _message_metadata(message)
    brain_status = str(metadata.get("brain_status", "") or "").strip().lower()
    if brain_status == "error":
        return True
    finish_reason = str(metadata.get("finish_reason", "") or "").strip().lower()
    return finish_reason == "error"


def _resolve_writer_attach_id(
    *,
    filtered_events: List[EventRecord],
    messages: List[Any],
) -> str:
    for event in reversed(filtered_events):
        if event.event_type == "client.attach":
            attach_id = str(event.payload.get("attach_id", "")).strip()
            if not attach_id:
                continue
            attach_role = str(event.payload.get("attach_role", "")).strip().lower()
            if attach_role and attach_role != ATTACH_ROLE_WRITER:
                continue
            return attach_id
    if messages:
        meta = getattr(messages[-1], "metadata", {}) or {}
        return str(meta.get("attach_id", "")).strip()
    return ""


def _resolve_thread_state(
    *,
    latest_run_event: Optional[RunEvent],
    delivery_state: str,
    latest_message: Any,
    filtered_events: List[EventRecord],
) -> tuple[str, str, str]:
    qualifier = ""
    pending_response_id = ""
    cancel_requested = any(
        event.event_type == "run.cancel_requested" for event in filtered_events
    )
    detached = any(event.event_type == "client.detached" for event in filtered_events)
    internal_error_outbound = _is_internal_error_outbound(latest_message)

    if latest_run_event is not None:
        state = latest_run_event.state
        if state == RUN_STATE_FAILED:
            return THREAD_STATE_FAILED, qualifier, pending_response_id
        if state == RUN_STATE_CANCELLED:
            return THREAD_STATE_CANCELLED, qualifier, pending_response_id
        if state in {
            RUN_STATE_RUNNING,
            RUN_STATE_RESPONDING,
            RUN_STATE_WAITING_TOOL,
            RUN_STATE_QUEUED,
        }:
            return THREAD_STATE_AWAITING, qualifier, pending_response_id
        if state == RUN_STATE_COMPLETED:
            if delivery_state in {DELIVERY_STATE_DELIVERED, DELIVERY_STATE_ACKED}:
                return THREAD_STATE_SETTLED, qualifier, pending_response_id
            if internal_error_outbound:
                return THREAD_STATE_SETTLED, "internal_error_outbound", ""
            if detached:
                qualifier = "detached_before_delivery"
            elif cancel_requested:
                qualifier = "cancel_requested"
            if latest_message is not None and latest_message.role == "outbound":
                pending_response_id = str(
                    getattr(latest_message, "id", "") or ""
                ).strip()
            if not pending_response_id:
                pending_response_id = latest_run_event.run_id
            return THREAD_STATE_RESPONSE_UNDELIVERED, qualifier, pending_response_id

    if latest_message is not None:
        if latest_message.role == "inbound":
            return THREAD_STATE_AWAITING, qualifier, pending_response_id
        if latest_message.role == "outbound":
            if internal_error_outbound:
                return THREAD_STATE_SETTLED, "internal_error_outbound", ""
            if delivery_state in {DELIVERY_STATE_NONE, DELIVERY_STATE_PERSISTED}:
                if detached:
                    qualifier = "detached_before_delivery"
                elif cancel_requested:
                    qualifier = "cancel_requested"
                pending_response_id = str(
                    getattr(latest_message, "id", "") or ""
                ).strip()
                return THREAD_STATE_RESPONSE_UNDELIVERED, qualifier, pending_response_id
            return THREAD_STATE_SETTLED, qualifier, pending_response_id

    if detached:
        return THREAD_STATE_DETACHED, qualifier, pending_response_id

    return THREAD_STATE_SETTLED, "no_state_events", pending_response_id


def _latest_event_time(events: List[EventRecord]) -> str:
    if not events:
        return ""
    return str(events[-1].created_at)


def _new_thread_id_for_decision(prefix: str) -> str:
    from uuid import uuid4

    safe_prefix = str(prefix or "thread").strip() or "thread"
    return f"{safe_prefix}:{uuid4().hex}"
