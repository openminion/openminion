from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from threading import Condition, Event, RLock
from typing import Any, Callable, Iterator
from uuid import uuid4

from openminion.base.time import utc_now_iso as _utc_now_iso
from openminion.modules.runtime.contracts import (
    TURN_STREAM_SCHEMA_VERSION,
    TurnChunk,
    TurnResponse,
)

from .constants import TURN_STREAM_HISTORY_LIMIT

ApprovalEventHook = Callable[[dict[str, Any]], None]


@dataclass
class _PendingApproval:
    approval_id: str
    tool_name: str
    consent_preview: str
    requested_at: str
    expires_at: str
    on_event: ApprovalEventHook | None
    ready: Event = field(default_factory=Event)
    outcome: str = "pending"
    decision: str | None = None
    resolved_at: str = ""


class TurnHandle:
    stream_schema_version = TURN_STREAM_SCHEMA_VERSION

    def __init__(
        self,
        *,
        trace_id: str,
        on_cancel: Callable[[str], bool],
        background: bool = False,
        agent_id: str = "",
        session_id: str = "",
        request_meta: dict[str, Any] | None = None,
    ) -> None:
        self.trace_id = trace_id
        self.agent_id = agent_id
        self.session_id = session_id
        self._request_meta = dict(request_meta or {})
        self._on_cancel = on_cancel
        self._background = background
        self._cancel_event = Event()
        self._result_ready = Event()
        self._result: TurnResponse | None = None
        self._stream_cv = Condition(RLock())
        self._history: deque[TurnChunk] = deque(maxlen=TURN_STREAM_HISTORY_LIMIT)
        self._next_sequence = 1
        self._primary_stream_claimed = False
        self._latest_phase_status: dict[str, Any] | None = None
        self._pending_approval: _PendingApproval | None = None

    @property
    def cancel_event(self) -> Event:
        return self._cancel_event

    @property
    def request_meta(self) -> dict[str, Any]:
        return dict(self._request_meta)

    def cancel(self) -> bool:
        self._cancel_event.set()
        self._settle_pending_approval(outcome="cancelled")
        return self._on_cancel(self.trace_id)

    def request_approval(
        self,
        *,
        tool_name: str,
        consent_preview: str,
        source_callback_id: str,
        timeout_s: float,
        on_event: ApprovalEventHook | None = None,
    ) -> bool:
        del source_callback_id
        if self._cancel_event.is_set() or self._result_ready.is_set():
            return False

        now = datetime.now(timezone.utc)
        wait_seconds = max(0.001, float(timeout_s))
        pending = _PendingApproval(
            approval_id=uuid4().hex,
            tool_name=str(tool_name or "").strip(),
            consent_preview=str(consent_preview or ""),
            requested_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=wait_seconds)).isoformat(),
            on_event=on_event,
        )
        with self._stream_cv:
            if (
                self._cancel_event.is_set()
                or self._result_ready.is_set()
                or self._pending_approval is not None
            ):
                return False
            self._pending_approval = pending
            self._emit_approval_event(pending, phase="requested")
        if not pending.ready.wait(timeout=wait_seconds):
            self._settle_pending_approval(
                outcome="expired",
                approval_id=pending.approval_id,
            )

        with self._stream_cv:
            approved = pending.outcome == "applied"
            if self._pending_approval is pending:
                self._pending_approval = None
        return approved

    def resolve_approval(self, *, approval_id: str, decision: str) -> bool:
        normalized_decision = str(decision or "").strip().lower()
        outcome = {
            "allow_once": "applied",
            "deny": "denied",
        }.get(normalized_decision)
        if outcome is None:
            return False
        return self._settle_pending_approval(
            outcome=outcome,
            decision=normalized_decision,
            approval_id=str(approval_id or "").strip(),
        )

    def _settle_pending_approval(
        self,
        *,
        outcome: str,
        decision: str | None = None,
        approval_id: str = "",
    ) -> bool:
        with self._stream_cv:
            pending = self._pending_approval
            if (
                pending is None
                or pending.outcome != "pending"
                or (approval_id and pending.approval_id != approval_id)
            ):
                return False
            pending.outcome = outcome
            pending.decision = decision
            pending.resolved_at = _utc_now_iso()
        try:
            self._emit_approval_event(pending, phase="resolved")
        finally:
            pending.ready.set()
        return True

    def _emit_approval_event(self, pending: _PendingApproval, *, phase: str) -> None:
        payload: dict[str, Any] = {
            "phase": phase,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "approval_id": pending.approval_id,
            "tool_name": pending.tool_name,
            "consent_preview": pending.consent_preview,
            "outcome": pending.outcome,
        }
        if phase == "requested":
            payload["requested_at"] = pending.requested_at
            payload["expires_at"] = pending.expires_at
        else:
            payload["decision"] = pending.decision
            payload["resolved_at"] = pending.resolved_at
        if pending.on_event is not None:
            pending.on_event(dict(payload))
        self._push_chunk(
            TurnChunk(trace_id=self.trace_id, kind="approval", data=payload)
        )

    def stream(self, timeout_s: float | None = None) -> Iterator[TurnChunk]:
        with self._stream_cv:
            if self._primary_stream_claimed:
                return
            self._primary_stream_claimed = True
        yield from self._iter_chunks(after_sequence=0, timeout_s=timeout_s)

    def subscribe(
        self,
        *,
        after_sequence: int = 0,
        timeout_s: float | None = None,
    ) -> Iterator[TurnChunk]:
        yield from self._iter_chunks(
            after_sequence=max(0, int(after_sequence)),
            timeout_s=timeout_s,
        )

    @property
    def replay_floor_sequence(self) -> int:
        with self._stream_cv:
            return self._history[0].sequence if self._history else self._next_sequence

    def current_phase_status(self) -> dict[str, Any] | None:
        with self._stream_cv:
            return (
                dict(self._latest_phase_status) if self._latest_phase_status else None
            )

    def result(self, timeout_s: float | None = None) -> TurnResponse:
        if not self._result_ready.wait(timeout=timeout_s):
            raise TimeoutError(f"turn result timed out trace_id={self.trace_id}")
        if self._result is None:
            raise RuntimeError(f"turn result missing trace_id={self.trace_id}")
        return self._result

    def _push_chunk(self, chunk: TurnChunk) -> None:
        with self._stream_cv:
            sequence = self._next_sequence
            self._next_sequence += 1
            sequenced = replace(
                chunk,
                trace_id=self.trace_id,
                schema_version=TURN_STREAM_SCHEMA_VERSION,
                sequence=sequence,
                event_id=f"{self.trace_id}:{sequence}",
            )
            self._history.append(sequenced)
            if sequenced.kind == "status":
                self._latest_phase_status = dict(sequenced.data)
            self._stream_cv.notify_all()

    def _set_result(self, response: TurnResponse) -> None:
        self._settle_pending_approval(outcome="cancelled")
        with self._stream_cv:
            self._result = response
            self._result_ready.set()
            self._stream_cv.notify_all()

    def _iter_chunks(
        self,
        *,
        after_sequence: int,
        timeout_s: float | None,
    ) -> Iterator[TurnChunk]:
        cursor = after_sequence
        while True:
            with self._stream_cv:
                chunk = next(
                    (item for item in self._history if item.sequence > cursor),
                    None,
                )
                if chunk is None:
                    if self._result_ready.is_set():
                        return
                    self._stream_cv.wait(timeout=timeout_s)
                    continue
            cursor = chunk.sequence
            yield chunk
