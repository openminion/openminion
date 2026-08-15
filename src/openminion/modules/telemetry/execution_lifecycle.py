from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any


def build_execution_traceparent(invocation_id: str, execution_id: str) -> str:
    trace_id = hashlib.sha256(str(invocation_id).encode("utf-8")).hexdigest()[:32]
    span_id = hashlib.sha256(str(execution_id).encode("utf-8")).hexdigest()[:16]
    return f"00-{trace_id}-{span_id}-01"


@dataclass(frozen=True)
class InvocationLifecycleFact:
    event_id: str
    timestamp: float
    event_type: str
    invocation_id: str
    session_id: str
    turn_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    trace_key: str | None = None
    execution_id: str | None = None
    agent_id: str | None = None
    mode: str | None = None


@dataclass(frozen=True)
class ExecutionTerminalFact:
    event_id: str
    timestamp: float
    event_type: str
    invocation_event_type: str
    resolved_state: str


class AgentExecutionTelemetry:
    def __init__(self, service: Any, *, inbound: Any) -> None:
        self._service = service
        self._inbound = inbound
        self._session_id = str(inbound.metadata.get("session_id") or "").strip()
        self._turn_id = str(
            inbound.metadata.get("turn_id")
            or inbound.metadata.get("request_id")
            or inbound.id
        )
        self._invocation_id = str(inbound.metadata.get("invocation_id") or "")
        self._execution_id = str(inbound.metadata.get("execution_id") or "")
        self._invocation_scope = str(inbound.metadata.get("invocation_scope") or "")
        inbound.metadata["turn_id"] = self._turn_id
        self._started_at = time.monotonic()
        self._active = bool(
            self._session_id and getattr(service, "_telemetryctl", None) is not None
        )

    async def start(self) -> None:
        if not self._active:
            return
        traceparent = str(self._inbound.metadata.get("traceparent") or "")
        if not traceparent and self._invocation_id and self._execution_id:
            traceparent = build_execution_traceparent(
                self._invocation_id,
                self._execution_id,
            )
            self._inbound.metadata["traceparent"] = traceparent
        self._service._bind_execution_telemetry(
            session_id=self._session_id,
            turn_id=self._turn_id,
            invocation_id=self._invocation_id,
            execution_id=self._execution_id,
        )
        started_at = time.time()
        source_event_id = f"agent.execution:{self._execution_id}:start"
        await self._emit(
            event_type="agent.execution.started",
            payload={
                "execution_id": self._execution_id,
                "agent_name": self._service._identity_agent_id,
                "traceparent": traceparent,
                "tracestate": str(self._inbound.metadata.get("tracestate") or ""),
            },
            status="started",
            event_id=source_event_id,
            timestamp=started_at,
        )
        if self._invocation_scope == "runtime":
            await self._service.emit_invocation_lifecycle(
                self._invocation_fact(
                    event_id=f"agent.invocation:{self._invocation_id}:start",
                    timestamp=started_at,
                    event_type="agent.invocation.started",
                    payload={
                        "scope": "runtime",
                        "source_event_id": source_event_id,
                        "source_event_type": "agent.execution.started",
                        "parent_invocation_id": None,
                        "run_id": self._inbound.metadata.get("run_id") or None,
                        "thread_id": self._inbound.metadata.get("thread_id") or None,
                    },
                )
            )
        for event_type, payload in (
            ("agent.turn.started", {"turn_operation_id": self._turn_id}),
            (
                "agent.phase.started",
                {"phase_id": f"{self._turn_id}:act", "phase": "act"},
            ),
        ):
            await self._emit(
                event_type=event_type,
                payload=payload,
                status="started",
            )
        if self._inbound.metadata.get("trace_context_status") == "invalid":
            await self._emit(
                event_type="telemetry.propagation.invalid",
                payload={"reason_code": "malformed_traceparent"},
                status="warning",
            )

    async def finish(self, response: Any) -> Any:
        if not self._active:
            return response
        duration_ms = self._duration_ms()
        for event_type, payload in (
            (
                "agent.phase.completed",
                {
                    "phase_id": f"{self._turn_id}:act",
                    "phase": "act",
                    "duration_ms": duration_ms,
                },
            ),
            (
                "agent.turn.completed",
                {"turn_operation_id": self._turn_id, "duration_ms": duration_ms},
            ),
        ):
            await self._emit(
                event_type=event_type,
                payload=payload,
                status="completed",
            )
        await self._emit_execution_terminal(
            terminal="completed",
            duration_ms=duration_ms,
            provider=str(getattr(response, "metadata", {}).get("provider") or ""),
            model=str(getattr(response, "metadata", {}).get("model") or ""),
        )
        self._unbind()
        return response

    async def fail(self, exc: BaseException) -> None:
        if not self._active:
            return
        duration_ms = self._duration_ms()
        error = {"type": type(exc).__name__}
        terminal = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
        for event_type, payload in (
            (
                "agent.phase.failed",
                {
                    "phase_id": f"{self._turn_id}:act",
                    "phase": "act",
                    "duration_ms": duration_ms,
                    "error": error,
                },
            ),
            (
                "agent.turn.failed",
                {
                    "turn_operation_id": self._turn_id,
                    "duration_ms": duration_ms,
                    "error": error,
                },
            ),
        ):
            await self._emit(
                event_type=event_type,
                payload=payload,
                status="failed",
            )
        await self._emit_execution_terminal(
            terminal=terminal,
            duration_ms=duration_ms,
            error=error,
        )
        self._unbind()

    async def _emit(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        status: str,
        event_id: str | None = None,
        timestamp: float | None = None,
    ) -> bool:
        return await self._service._emit_agent_event(
            session_id=self._session_id,
            turn_id=self._turn_id,
            event_type=event_type,
            payload=payload,
            status=status,
            event_id=event_id,
            timestamp=timestamp,
            invocation_id=self._invocation_id,
            execution_id=self._execution_id,
        )

    async def _emit_execution_terminal(
        self,
        *,
        terminal: str,
        duration_ms: float,
        error: dict[str, Any] | None = None,
        provider: str = "",
        model: str = "",
    ) -> None:
        timestamp = time.time()
        fact = ExecutionTerminalFact(
            event_id=f"agent.execution:{self._execution_id}:terminal",
            timestamp=timestamp,
            event_type=f"agent.execution.{terminal}",
            invocation_event_type=f"agent.invocation.{terminal}",
            resolved_state=terminal,
        )
        await self._emit(
            event_type=fact.event_type,
            payload={
                "execution_id": self._execution_id,
                "duration_ms": duration_ms,
                **({"error": error} if error else {}),
            },
            status=terminal,
            event_id=fact.event_id,
            timestamp=fact.timestamp,
        )
        if self._invocation_scope == "runtime":
            await self._service.emit_invocation_lifecycle(
                self._invocation_fact(
                    event_id=f"agent.invocation:{self._invocation_id}:terminal",
                    timestamp=fact.timestamp,
                    event_type=fact.invocation_event_type,
                    payload={
                        "scope": "runtime",
                        "source_event_id": fact.event_id,
                        "source_event_type": fact.event_type,
                        "resolved_state": fact.resolved_state,
                        "run_id": self._inbound.metadata.get("run_id") or None,
                        "thread_id": self._inbound.metadata.get("thread_id") or None,
                        "provider": provider or None,
                        "model": model or None,
                    },
                )
            )

    def _invocation_fact(
        self,
        *,
        event_id: str,
        timestamp: float,
        event_type: str,
        payload: dict[str, Any],
    ) -> InvocationLifecycleFact:
        return InvocationLifecycleFact(
            event_id=event_id,
            timestamp=timestamp,
            event_type=event_type,
            invocation_id=self._invocation_id,
            session_id=self._session_id,
            turn_id=self._turn_id,
            execution_id=self._execution_id,
            agent_id=self._service._identity_agent_id,
            payload=payload,
        )

    def _duration_ms(self) -> float:
        return (time.monotonic() - self._started_at) * 1000

    def _unbind(self) -> None:
        self._service._unbind_execution_telemetry(
            session_id=self._session_id,
            turn_id=self._turn_id,
        )


__all__ = [
    "AgentExecutionTelemetry",
    "ExecutionTerminalFact",
    "InvocationLifecycleFact",
]
