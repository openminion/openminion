from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator, Literal


ChatPhase = Literal[
    "runtime_bootstrap",
    "daemon_probe_start",
    "session_resume",
    "memory_retrieval",
    "context_pack_build",
    "gateway_routing",
    "gateway_session_context",
    "brain_state_load",
    "brain_pre_dispatch",
    "brain_budget_check",
    "brain_confirmation",
    "gateway_agent_dispatch",
    "brain_tick_dispatch",
    "tool_schema_serialization",
    "provider_request_build",
    "provider_round_trip",
    "approval_wait",
    "tool_calls",
    "response_normalization",
    "response_persistence",
    "memory_write",
    "cli_render_delivery",
]


CHAT_PHASES: tuple[str, ...] = (
    "runtime_bootstrap",
    "daemon_probe_start",
    "session_resume",
    "memory_retrieval",
    "context_pack_build",
    "gateway_routing",
    "gateway_session_context",
    "brain_state_load",
    "brain_pre_dispatch",
    "brain_budget_check",
    "brain_confirmation",
    "gateway_agent_dispatch",
    "brain_tick_dispatch",
    "tool_schema_serialization",
    "provider_request_build",
    "provider_round_trip",
    "approval_wait",
    "tool_calls",
    "response_normalization",
    "response_persistence",
    "memory_write",
    "cli_render_delivery",
)

_ACTIVE_CHAT_PHASE_TIMER: ContextVar["ChatPhaseTimer | None"] = ContextVar(
    "openminion_active_chat_phase_timer",
    default=None,
)


@dataclass(frozen=True)
class ChatPhaseTimingPayload:
    """Chatphasetimingpayload contract."""

    cold_start: bool
    total_turn_ms: int
    time_to_first_text_ms: int | None

    runtime_bootstrap_ms: int = 0
    daemon_probe_start_ms: int = 0
    session_resume_ms: int = 0
    memory_retrieval_ms: int = 0
    context_pack_build_ms: int = 0
    gateway_routing_ms: int = 0
    gateway_session_context_ms: int = 0
    brain_state_load_ms: int = 0
    brain_pre_dispatch_ms: int = 0
    brain_budget_check_ms: int = 0
    brain_confirmation_ms: int = 0
    gateway_agent_dispatch_ms: int = 0
    brain_tick_dispatch_ms: int = 0
    tool_schema_serialization_ms: int = 0
    provider_request_build_ms: int = 0
    provider_round_trip_ms: int = 0
    approval_wait_ms: int = 0
    tool_calls_ms: int = 0
    response_normalization_ms: int = 0
    response_persistence_ms: int = 0
    memory_write_ms: int = 0
    cli_render_delivery_ms: int = 0

    phases_instrumented: tuple[str, ...] = field(default_factory=tuple)
    turn_id: str = ""
    session_id: str = ""
    agent_id: str = ""
    process_mode: str = ""
    transport: str = ""

    def __post_init__(self) -> None:  # pragma: no cover - simple guards
        if self.total_turn_ms < 0:
            raise ValueError("total_turn_ms must be >= 0")
        if self.time_to_first_text_ms is not None and self.time_to_first_text_ms < 0:
            raise ValueError("time_to_first_text_ms must be >= 0 or None")
        for phase in CHAT_PHASES:
            value = getattr(self, f"{phase}_ms")
            if value < 0:
                raise ValueError(f"{phase}_ms must be >= 0; got {value!r}")

    def as_dict(self) -> dict[str, object]:
        """Render as a JSON-friendly dict for `emit_canonical_event`."""

        payload: dict[str, object] = {
            "cold_start": bool(self.cold_start),
            "total_turn_ms": int(self.total_turn_ms),
            "time_to_first_text_ms": (
                None
                if self.time_to_first_text_ms is None
                else int(self.time_to_first_text_ms)
            ),
            "phases_instrumented": list(self.phases_instrumented),
            "turn_id": str(self.turn_id),
            "session_id": str(self.session_id),
            "agent_id": str(self.agent_id),
            "process_mode": str(self.process_mode),
            "transport": str(self.transport),
        }
        for phase in CHAT_PHASES:
            payload[f"{phase}_ms"] = int(getattr(self, f"{phase}_ms"))
        return payload


@dataclass
class ChatPhaseTimer:
    """Chatphasetimer contract."""

    cold_start: bool = False
    _turn_start: float = field(default_factory=time.perf_counter)
    _phase_elapsed_ns: dict[str, int] = field(default_factory=dict)
    _instrumented: set[str] = field(default_factory=set)
    _first_text_ns: int | None = None

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        if name not in CHAT_PHASES:
            raise ValueError(
                f"Unknown chat phase: {name!r}. Allowed: {sorted(CHAT_PHASES)}"
            )
        start = time.perf_counter_ns()
        self._instrumented.add(name)
        try:
            yield
        finally:
            elapsed = time.perf_counter_ns() - start
            self._phase_elapsed_ns[name] = self._phase_elapsed_ns.get(name, 0) + elapsed

    def mark_first_text(self) -> None:
        """Record the wall-clock moment the first text byte became visible.

        Called by the CLI render layer (CRTL-09) once streaming lands.
        Idempotent — the first call wins; subsequent calls are no-ops.
        """

        if self._first_text_ns is None:
            self._first_text_ns = time.perf_counter_ns() - int(
                self._turn_start * 1_000_000_000
            )

    def stop(self) -> int:
        """Return total wall-clock ms since timer construction."""

        return int((time.perf_counter() - self._turn_start) * 1000)

    def build_payload(
        self,
        *,
        turn_id: str = "",
        session_id: str = "",
        agent_id: str = "",
        process_mode: str = "",
        transport: str = "",
    ) -> ChatPhaseTimingPayload:
        """Assemble the typed payload from accumulated checkpoints."""

        total_ms = self.stop()
        per_phase_ms: dict[str, int] = {
            phase: int(self._phase_elapsed_ns.get(phase, 0) // 1_000_000)
            for phase in CHAT_PHASES
        }
        ttft = (
            None
            if self._first_text_ns is None
            else int(self._first_text_ns // 1_000_000)
        )
        return ChatPhaseTimingPayload(
            cold_start=self.cold_start,
            total_turn_ms=total_ms,
            time_to_first_text_ms=ttft,
            phases_instrumented=tuple(sorted(self._instrumented)),
            turn_id=turn_id,
            session_id=session_id,
            agent_id=agent_id,
            process_mode=process_mode,
            transport=transport,
            **{f"{phase}_ms": ms for phase, ms in per_phase_ms.items()},
        )


__all__ = [
    "CHAT_PHASES",
    "ChatPhase",
    "ChatPhaseTimer",
    "ChatPhaseTimingPayload",
    "active_chat_phase",
    "mark_active_chat_first_text",
    "use_chat_phase_timer",
]


def _validate_phase_name(name: str) -> None:
    if name not in CHAT_PHASES:
        raise ValueError(f"Unknown chat phase: {name!r}. Allowed: {sorted(CHAT_PHASES)}")


@contextmanager
def use_chat_phase_timer(timer: ChatPhaseTimer) -> Iterator[ChatPhaseTimer]:
    token = _ACTIVE_CHAT_PHASE_TIMER.set(timer)
    try:
        yield timer
    finally:
        _ACTIVE_CHAT_PHASE_TIMER.reset(token)


@contextmanager
def active_chat_phase(name: str) -> Iterator[None]:
    _validate_phase_name(name)
    timer = _ACTIVE_CHAT_PHASE_TIMER.get()
    if timer is None:
        yield
        return
    with timer.phase(name):
        yield


def mark_active_chat_first_text() -> None:
    timer = _ACTIVE_CHAT_PHASE_TIMER.get()
    if timer is not None:
        timer.mark_first_text()
