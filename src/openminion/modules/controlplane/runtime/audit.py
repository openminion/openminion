import logging
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

from openminion.base.time import utc_now_iso as _iso_now


def emit_audit_event(
    audit_logger: object | None, event_type: str, **details: object
) -> None:
    if audit_logger is None:
        return
    if hasattr(audit_logger, "emit"):
        audit_logger.emit(event_type, details=dict(details))
        return
    if hasattr(audit_logger, "log"):
        audit_logger.log(event_type, **details)


@dataclass
class AuditEvent:
    event_type: str
    outcome: str = "ok"
    severity: str = "info"
    chat_key: str | None = None
    user_key: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    span_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = field(default_factory=_iso_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "outcome": self.outcome,
            "severity": self.severity,
            "chat_key": self.chat_key,
            "user_key": self.user_key,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "details": self.details,
            "error": self.error,
        }


class AuditLogger:
    def __init__(self, sink: Any = None) -> None:
        self.events: list[AuditEvent] = []
        self._sink = sink  # callable(AuditEvent) -> None or None
        self._logger = logging.getLogger(__name__)
        self._failures = 0
        self._last_error: str | None = None
        self._wizard_step_failures = 0

    def emit(
        self,
        event_type: str,
        *,
        outcome: str = "ok",
        severity: str = "info",
        chat_key: str | None = None,
        user_key: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        details: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> AuditEvent:
        ev = AuditEvent(
            event_type=event_type,
            outcome=outcome,
            severity=severity,
            chat_key=chat_key,
            user_key=user_key,
            session_id=session_id,
            agent_id=agent_id,
            trace_id=trace_id or uuid.uuid4().hex,
            span_id=span_id,
            details=details or {},
            error=error,
        )
        self.events.append(ev)
        if ev.event_type == "cp.wizard.step.failed":
            self._wizard_step_failures += 1
        if self._sink is not None:
            try:
                self._sink(ev)
            except Exception as exc:  # noqa: BLE001
                self._failures += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._logger.exception(
                    "audit.sink.failure",
                    extra={
                        "event_type": ev.event_type,
                        "session_id": ev.session_id,
                        "trace_id": ev.trace_id,
                        "failure_count": self._failures,
                    },
                )
                if self._failures % 100 == 0:
                    sys.stderr.write(
                        "audit.sink.failure "
                        f"count={self._failures} "
                        f"last_error={self._last_error}\n"
                    )
        return ev

    def log(self, event: str, **payload: object) -> None:
        self.emit(event, details=dict(payload))

    def list_events(
        self,
        *,
        event_type: str | None = None,
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> list[AuditEvent]:
        out = list(self.events)
        if event_type:
            out = [e for e in out if e.event_type == event_type]
        if session_id:
            out = [e for e in out if e.session_id == session_id]
        if trace_id:
            out = [e for e in out if e.trace_id == trace_id]
        return out

    def failure_count(self) -> int:
        return self._failures

    def health_status(self) -> dict[str, Any]:
        return {
            "healthy": self._failures == 0,
            "failures": self._failures,
            "last_error": self._last_error,
            "wizard_step_failures": self._wizard_step_failures,
        }
