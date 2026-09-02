from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from openminion.modules.telemetry.constants import (
    INVOCATION_LIFECYCLE_REPAIR_DIAGNOSTIC_LIMIT,
    INVOCATION_LIFECYCLE_REPAIR_SCHEMA,
)


REPAIR_SCHEMA_VERSION = INVOCATION_LIFECYCLE_REPAIR_SCHEMA
_TERMINAL_SOURCE_TYPES = {
    "response.delivered",
    "response.acked",
    "run.completed",
    "run.failed",
    "run.cancelled",
}


@dataclass
class InvocationLifecycleRepairReport:
    session_id: str
    high_water_event_id: int | None
    status: str = "unchanged"
    created_count: int = 0
    identical_count: int = 0
    invalid_count: int = 0
    conflict_count: int = 0
    failed_count: int = 0
    diagnostics: list[dict[str, object]] = field(default_factory=list)
    diagnostics_truncated: bool = False

    def add_diagnostic(
        self,
        code: str,
        *,
        event_id: str | None,
        source_event_id: int | None,
    ) -> None:
        if len(self.diagnostics) < INVOCATION_LIFECYCLE_REPAIR_DIAGNOSTIC_LIMIT:
            self.diagnostics.append(
                {
                    "code": code,
                    "event_id": event_id,
                    "source_event_id": source_event_id,
                }
            )
        else:
            self.diagnostics_truncated = True

    def finalize(self) -> "InvocationLifecycleRepairReport":
        self.diagnostics.sort(
            key=lambda row: (
                int(row["source_event_id"] or -1),
                str(row["code"]),
            )
        )
        if self.status == "not_found":
            return self
        if self.failed_count:
            self.status = "error"
        elif self.conflict_count:
            self.status = "conflict"
        elif self.invalid_count:
            self.status = "invalid_source"
        elif self.created_count:
            self.status = "repaired"
        else:
            self.status = "unchanged"
        return self

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": REPAIR_SCHEMA_VERSION,
            "session_id": self.session_id,
            "high_water_event_id": self.high_water_event_id,
            "status": self.status,
            "created_count": self.created_count,
            "identical_count": self.identical_count,
            "invalid_count": self.invalid_count,
            "conflict_count": self.conflict_count,
            "failed_count": self.failed_count,
            "diagnostics": self.diagnostics,
            "diagnostics_truncated": self.diagnostics_truncated,
        }


class InvocationLifecycleReconciler:
    @classmethod
    def for_runtime(cls, *, sessions: Any, telemetryctl: Any | None):
        from openminion.modules.storage.runtime.session_store import (
            RuntimeSessionTurnBusyError,
            agent_id_from_session_key,
            is_room_session_key,
        )
        from openminion.modules.task.run.status import resolve_invocation_terminal

        return cls(
            sessions=sessions,
            telemetryctl=telemetryctl,
            resolve_terminal=resolve_invocation_terminal,
            busy_error=RuntimeSessionTurnBusyError,
            agent_id_from_session_key=agent_id_from_session_key,
            is_room_session_key=is_room_session_key,
        )

    def __init__(
        self,
        *,
        sessions: Any,
        telemetryctl: Any | None,
        resolve_terminal: Callable[..., Any],
        busy_error: type[Exception],
        agent_id_from_session_key: Callable[[str], str],
        is_room_session_key: Callable[[str], bool],
    ) -> None:
        self._sessions = sessions
        self._telemetryctl = telemetryctl
        self._resolve_terminal = resolve_terminal
        self._busy_error = busy_error
        self._agent_id_from_session_key = agent_id_from_session_key
        self._is_room_session_key = is_room_session_key

    def repair_session(self, session_id: str) -> InvocationLifecycleRepairReport:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        session = self._sessions.get_session(normalized_session_id)
        if session is None:
            report = InvocationLifecycleRepairReport(normalized_session_id, None)
            report.status = "not_found"
            report.add_diagnostic(
                "SESSION_NOT_FOUND",
                event_id=None,
                source_event_id=None,
            )
            return report

        high_water = self._sessions.event_high_water(session_id=normalized_session_id)
        report = InvocationLifecycleRepairReport(normalized_session_id, high_water)
        lease = None
        acquire_lease = getattr(self._sessions, "acquire_session_turn_lease", None)
        if callable(acquire_lease):
            request_id = f"lifecycle-repair:{uuid4().hex}"
            try:
                lease = acquire_lease(
                    normalized_session_id,
                    owner=request_id,
                    request_id=request_id,
                    ttl_s=60,
                )
            except self._busy_error:
                report.failed_count = 1
                report.add_diagnostic(
                    "TELEMETRY_STORAGE_FAILED",
                    event_id=None,
                    source_event_id=None,
                )
                return report.finalize()
        try:
            self._repair_events(
                session=session,
                high_water=high_water,
                report=report,
            )
        finally:
            if lease is not None:
                self._sessions.release_session_turn_lease(
                    normalized_session_id,
                    owner=lease.owner,
                    fence_token=lease.fence_token,
                )
        return report.finalize()

    def _repair_events(
        self,
        *,
        session: Any,
        high_water: int,
        report: InvocationLifecycleRepairReport,
    ) -> None:
        after_id = 0
        repaired_starts: set[str] = set()
        repaired_terminals: set[str] = set()
        while after_id < high_water:
            page = self._sessions.list_events_after_id(
                session_id=report.session_id,
                after_id=after_id,
                high_water_id=high_water,
                limit=1000,
            )
            if not page:
                return
            for event in page:
                self._repair_source_event(
                    session=session,
                    event=event,
                    report=report,
                    repaired_starts=repaired_starts,
                    repaired_terminals=repaired_terminals,
                )
            after_id = int(page[-1].id)

    def _repair_source_event(
        self,
        *,
        session: Any,
        event: Any,
        report: InvocationLifecycleRepairReport,
        repaired_starts: set[str],
        repaired_terminals: set[str],
    ) -> None:
        if event.event_type == "run.queued":
            invocation_id = str(event.payload.get("invocation_id", "") or "").strip()
            if invocation_id and invocation_id not in repaired_starts:
                repaired_starts.add(invocation_id)
                self._repair_start(
                    session=session,
                    event=event,
                    invocation_id=invocation_id,
                    report=report,
                )
        if event.event_type not in _TERMINAL_SOURCE_TYPES:
            return
        projection = self._resolve_terminal(
            self._sessions,
            session_id=event.session_id,
            trigger_event=event,
            conversation_id=str(event.payload.get("conversation_id", "") or ""),
            thread_id=str(event.payload.get("thread_id", "") or ""),
        )
        if projection is None or projection.invocation_id in repaired_terminals:
            return
        repaired_terminals.add(projection.invocation_id)
        event_types = {
            "settled": "agent.invocation.completed",
            "failed": "agent.invocation.failed",
            "cancelled": "agent.invocation.cancelled",
        }
        event_id = f"agent.invocation:{projection.invocation_id}:terminal"
        status = self._repair_fact(
            session_id=event.session_id,
            turn_id=str(event.payload.get("request_id") or projection.run_id),
            event_type=event_types[projection.resolved_state],
            payload={
                "scope": "durable",
                "source_event_id": projection.source_event_id,
                "source_event_type": projection.source_event_type,
                "resolved_state": projection.resolved_state,
                "run_id": projection.run_id,
                "thread_id": projection.thread_id,
                "provider": event.payload.get("provider") or None,
                "model": event.payload.get("model") or None,
                "error_code": event.payload.get("error_code") or None,
            },
            event_id=event_id,
            timestamp=datetime.fromisoformat(projection.source_timestamp).timestamp(),
            invocation_id=projection.invocation_id,
            agent_id=None,
        )
        self._record_status(report, status, event_id=event_id, source=event)

    def _repair_start(
        self,
        *,
        session: Any,
        event: Any,
        invocation_id: str,
        report: InvocationLifecycleRepairReport,
    ) -> None:
        raw_agent_id = event.payload.get("agent_id")
        if raw_agent_id is not None and not isinstance(raw_agent_id, str):
            report.invalid_count += 1
            report.add_diagnostic(
                "SOURCE_IDENTITY_MALFORMED",
                event_id=f"agent.invocation:{invocation_id}:start",
                source_event_id=event.id,
            )
            return
        agent_id = str(raw_agent_id or "").strip()
        if not agent_id and not self._is_room_session_key(session.session_key):
            agent_id = self._agent_id_from_session_key(session.session_key)
        if not agent_id:
            report.invalid_count += 1
            report.add_diagnostic(
                "SOURCE_IDENTITY_MISSING",
                event_id=f"agent.invocation:{invocation_id}:start",
                source_event_id=event.id,
            )
            return
        event_id = f"agent.invocation:{invocation_id}:start"
        payload: dict[str, object] = {
            "scope": "durable",
            "source_event_id": event.id,
            "source_event_type": "run.queued",
            "run_id": str(event.payload.get("run_id", "") or ""),
            "thread_id": str(event.payload.get("thread_id", "") or ""),
        }
        parent_invocation_id = str(
            event.payload.get("parent_invocation_id", "") or ""
        ).strip()
        if parent_invocation_id:
            payload["parent_invocation_id"] = parent_invocation_id
        status = self._repair_fact(
            session_id=event.session_id,
            turn_id=str(event.payload.get("request_id", "") or ""),
            event_type="agent.invocation.started",
            payload=payload,
            event_id=event_id,
            timestamp=datetime.fromisoformat(event.created_at).timestamp(),
            invocation_id=invocation_id,
            agent_id=agent_id,
        )
        self._record_status(report, status, event_id=event_id, source=event)

    def _repair_fact(self, **kwargs: Any) -> str:
        repair = getattr(self._telemetryctl, "repair_canonical_event_sync", None)
        if not callable(repair):
            return "storage_failed"
        return str(repair(**kwargs))

    @staticmethod
    def _record_status(
        report: InvocationLifecycleRepairReport,
        status: str,
        *,
        event_id: str,
        source: Any,
    ) -> None:
        if status == "created":
            report.created_count += 1
            return
        if status == "already_identical":
            report.identical_count += 1
            return
        if status == "conflict":
            report.conflict_count += 1
            report.add_diagnostic(
                "LIFECYCLE_CONFLICT",
                event_id=event_id,
                source_event_id=source.id,
            )
            return
        report.failed_count += 1
        report.add_diagnostic(
            "TELEMETRY_STORAGE_FAILED",
            event_id=event_id,
            source_event_id=source.id,
        )


__all__ = [
    "InvocationLifecycleReconciler",
    "InvocationLifecycleRepairReport",
    "REPAIR_SCHEMA_VERSION",
]
