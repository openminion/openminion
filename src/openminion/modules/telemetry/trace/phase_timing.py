from __future__ import annotations

import json
import threading
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
    "session_compaction",
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
    "memory_followup_flush",
    "memory_summary_checkpoint",
    "memory_summary_structure",
    "run_record_finish",
    "response_delivery",
    "response_delivered_event",
    "terminal_event",
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
    "session_compaction",
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
    "memory_followup_flush",
    "memory_summary_checkpoint",
    "memory_summary_structure",
    "run_record_finish",
    "response_delivery",
    "response_delivered_event",
    "terminal_event",
    "cli_render_delivery",
)

_ACTIVE_CHAT_PHASE_TIMER: ContextVar["ChatPhaseTimer | None"] = ContextVar(
    "openminion_active_chat_phase_timer",
    default=None,
)


@dataclass(frozen=True)
class ChatPhaseTimingPayload:
    """Immutable timing and provider-cost facts for one interactive turn."""

    cold_start: bool
    total_turn_ms: int
    time_to_first_text_ms: int | None
    provider_token_ttft_ms: int | None = None

    runtime_bootstrap_ms: int = 0
    daemon_probe_start_ms: int = 0
    session_resume_ms: int = 0
    memory_retrieval_ms: int = 0
    context_pack_build_ms: int = 0
    gateway_routing_ms: int = 0
    gateway_session_context_ms: int = 0
    session_compaction_ms: int = 0
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
    memory_followup_flush_ms: int = 0
    memory_summary_checkpoint_ms: int = 0
    memory_summary_structure_ms: int = 0
    run_record_finish_ms: int = 0
    response_delivery_ms: int = 0
    response_delivered_event_ms: int = 0
    terminal_event_ms: int = 0
    cli_render_delivery_ms: int = 0

    phases_instrumented: tuple[str, ...] = field(default_factory=tuple)
    turn_id: str = ""
    session_id: str = ""
    agent_id: str = ""
    process_mode: str = ""
    transport: str = ""
    provider_calls_total: int = 0
    provider_call_purposes: tuple[str, ...] = field(default_factory=tuple)
    provider_call_latency_ms: tuple[int, ...] = field(default_factory=tuple)
    provider_attempts: tuple[dict[str, object], ...] = field(default_factory=tuple)
    provider_request_bytes: int | None = None
    provider_response_bytes: int | None = None
    provider_input_tokens: int | None = None
    provider_output_tokens: int | None = None
    tool_schema_count_max: int | None = None
    tool_schema_bytes_total: int | None = None

    def __post_init__(self) -> None:  # pragma: no cover - simple guards
        if self.total_turn_ms < 0:
            raise ValueError("total_turn_ms must be >= 0")
        if self.time_to_first_text_ms is not None and self.time_to_first_text_ms < 0:
            raise ValueError("time_to_first_text_ms must be >= 0 or None")
        if self.provider_token_ttft_ms is not None and self.provider_token_ttft_ms < 0:
            raise ValueError("provider_token_ttft_ms must be >= 0 or None")
        for phase in CHAT_PHASES:
            value = getattr(self, f"{phase}_ms")
            if value < 0:
                raise ValueError(f"{phase}_ms must be >= 0; got {value!r}")
        if any(value < 0 for value in self.provider_call_latency_ms):
            raise ValueError("provider_call_latency_ms values must be >= 0")
        if self.provider_call_latency_ms and (
            len(self.provider_call_latency_ms) != len(self.provider_call_purposes)
        ):
            raise ValueError(
                "provider_call_latency_ms must align with provider_call_purposes"
            )
        for attempt in self.provider_attempts:
            attempt_number = attempt.get("attempt")
            if isinstance(attempt_number, int) and attempt_number < 1:
                raise ValueError("provider_attempts attempt must be >= 1")
            latency_ms = attempt.get("latency_ms")
            if isinstance(latency_ms, int) and latency_ms < 0:
                raise ValueError("provider_attempts latency_ms must be >= 0")

    def as_dict(self) -> dict[str, object]:
        """Render as a JSON-friendly dict for `emit_canonical_event`."""

        payload: dict[str, object] = {
            "cold_start": self.cold_start,
            "total_turn_ms": self.total_turn_ms,
            "time_to_first_text_ms": self.time_to_first_text_ms,
            "provider_token_ttft_ms": self.provider_token_ttft_ms,
            "phases_instrumented": list(self.phases_instrumented),
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "process_mode": self.process_mode,
            "transport": self.transport,
            "provider_calls_total": self.provider_calls_total,
            "provider_call_purposes": list(self.provider_call_purposes),
            "provider_call_latency_ms": list(self.provider_call_latency_ms),
            "provider_attempts": [dict(item) for item in self.provider_attempts],
            "provider_request_bytes": self.provider_request_bytes,
            "provider_response_bytes": self.provider_response_bytes,
            "provider_input_tokens": self.provider_input_tokens,
            "provider_output_tokens": self.provider_output_tokens,
            "tool_schema_count_max": self.tool_schema_count_max,
            "tool_schema_bytes_total": self.tool_schema_bytes_total,
        }
        for phase in CHAT_PHASES:
            payload[f"{phase}_ms"] = int(getattr(self, f"{phase}_ms"))
        return payload


@dataclass
class ChatPhaseTimer:
    """Accumulate phase durations and first-output/provider-call timing."""

    cold_start: bool = False
    _turn_start: float = field(default_factory=time.perf_counter)
    _phase_elapsed_ns: dict[str, int] = field(default_factory=dict)
    _instrumented: set[str] = field(default_factory=set)
    _first_text_ns: int | None = None
    _first_provider_token_ns: int | None = None
    _provider_call_purposes: list[str] = field(default_factory=list)
    _provider_call_latency_ms: list[int] = field(default_factory=list)
    _provider_attempts: list[dict[str, object]] = field(default_factory=list)
    _provider_request_bytes: int = 0
    _provider_response_bytes: int = 0
    _provider_input_tokens: int = 0
    _provider_output_tokens: int = 0
    _tool_schema_count_max: int = 0
    _tool_schema_bytes_total: int = 0
    _has_provider_request_bytes: bool = False
    _has_provider_response_bytes: bool = False
    _has_provider_input_tokens: bool = False
    _has_provider_output_tokens: bool = False
    _has_tool_schema_metrics: bool = False
    _lock: threading.RLock = field(default_factory=threading.RLock)

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        _validate_phase_name(name)
        start = time.perf_counter_ns()
        with self._lock:
            self._instrumented.add(name)
        try:
            yield
        finally:
            elapsed = time.perf_counter_ns() - start
            with self._lock:
                self._phase_elapsed_ns[name] = (
                    self._phase_elapsed_ns.get(name, 0) + elapsed
                )

    def mark_first_text(self) -> None:
        """Record the first visible-text timestamp; the first call wins."""

        with self._lock:
            if self._first_text_ns is None:
                self._first_text_ns = time.perf_counter_ns() - int(
                    self._turn_start * 1_000_000_000
                )

    def mark_provider_token(self) -> None:
        with self._lock:
            if self._first_provider_token_ns is None:
                self._first_provider_token_ns = time.perf_counter_ns() - int(
                    self._turn_start * 1_000_000_000
                )

    def record_provider_call(
        self,
        *,
        purpose: str,
        messages: list[object],
        tools: list[object],
        response: object,
    ) -> None:
        normalized_purpose = str(purpose or "unknown").strip() or "unknown"
        message_payload = [_jsonable(item) for item in messages]
        tool_payload = [_jsonable(item) for item in tools]
        with self._lock:
            self._provider_call_purposes.append(normalized_purpose)
            self._provider_call_latency_ms.append(
                max(0, int(getattr(response, "latency_ms", 0) or 0))
            )
            self._provider_request_bytes += _json_bytes(
                {"messages": message_payload, "tools": tool_payload}
            )
            self._has_provider_request_bytes = True
            self._provider_response_bytes += _json_bytes(
                {
                    "output_text": str(getattr(response, "output_text", "") or ""),
                    "tool_calls": [
                        _jsonable(item)
                        for item in list(getattr(response, "tool_calls", []) or [])
                    ],
                    "finish_reason": str(getattr(response, "finish_reason", "") or ""),
                }
            )
            self._has_provider_response_bytes = True
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        with self._lock:
            if input_tokens is not None:
                self._provider_input_tokens += max(0, int(input_tokens))
                self._has_provider_input_tokens = True
            if output_tokens is not None:
                self._provider_output_tokens += max(0, int(output_tokens))
                self._has_provider_output_tokens = True
            self._tool_schema_count_max = max(self._tool_schema_count_max, len(tools))
            self._tool_schema_bytes_total += _json_bytes(tool_payload)
            self._has_tool_schema_metrics = True

    def record_provider_attempt(
        self,
        *,
        logical_call_id: str,
        semantic_purpose: str,
        attempt: int,
        provider: str,
        model: str,
        route_posture: str,
        attempt_posture: str,
        latency_ms: int,
        outcome: str,
        error_code: str = "",
    ) -> None:
        payload: dict[str, object] = {
            "logical_call_id": str(logical_call_id or "unavailable").strip()
            or "unavailable",
            "semantic_purpose": str(semantic_purpose or "unknown").strip() or "unknown",
            "attempt": max(1, int(attempt or 1)),
            "provider": str(provider or "unavailable").strip() or "unavailable",
            "model": str(model or "unavailable").strip() or "unavailable",
            "route_posture": str(route_posture or "primary").strip() or "primary",
            "attempt_posture": str(attempt_posture or "initial").strip() or "initial",
            "latency_ms": max(0, int(latency_ms or 0)),
            "outcome": str(outcome or "unknown").strip() or "unknown",
        }
        normalized_error = str(error_code or "").strip().upper()
        if normalized_error:
            payload["error_code"] = normalized_error
        with self._lock:
            self._provider_attempts.append(payload)

    def stop(self) -> int:
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
        total_ms = self.stop()
        with self._lock:
            per_phase_ms: dict[str, int] = {
                phase: int(self._phase_elapsed_ns.get(phase, 0) // 1_000_000)
                for phase in CHAT_PHASES
            }
            ttft = (
                None
                if self._first_text_ns is None
                else int(self._first_text_ns // 1_000_000)
            )
            provider_ttft = (
                None
                if self._first_provider_token_ns is None
                else int(self._first_provider_token_ns // 1_000_000)
            )
            instrumented = tuple(sorted(self._instrumented))
            provider_call_purposes = tuple(self._provider_call_purposes)
            provider_call_latency_ms = tuple(self._provider_call_latency_ms)
            provider_attempts = tuple(dict(item) for item in self._provider_attempts)
            provider_request_bytes = self._provider_request_bytes
            has_provider_request_bytes = self._has_provider_request_bytes
            provider_response_bytes = self._provider_response_bytes
            has_provider_response_bytes = self._has_provider_response_bytes
            provider_input_tokens = self._provider_input_tokens
            has_provider_input_tokens = self._has_provider_input_tokens
            provider_output_tokens = self._provider_output_tokens
            has_provider_output_tokens = self._has_provider_output_tokens
            tool_schema_count_max = self._tool_schema_count_max
            tool_schema_bytes_total = self._tool_schema_bytes_total
            has_tool_schema_metrics = self._has_tool_schema_metrics
        return ChatPhaseTimingPayload(
            cold_start=self.cold_start,
            total_turn_ms=total_ms,
            time_to_first_text_ms=ttft,
            provider_token_ttft_ms=provider_ttft,
            phases_instrumented=instrumented,
            turn_id=turn_id,
            session_id=session_id,
            agent_id=agent_id,
            process_mode=process_mode,
            transport=transport,
            provider_calls_total=len(provider_call_purposes),
            provider_call_purposes=provider_call_purposes,
            provider_call_latency_ms=provider_call_latency_ms,
            provider_attempts=provider_attempts,
            provider_request_bytes=(
                provider_request_bytes if has_provider_request_bytes else None
            ),
            provider_response_bytes=(
                provider_response_bytes if has_provider_response_bytes else None
            ),
            provider_input_tokens=(
                provider_input_tokens if has_provider_input_tokens else None
            ),
            provider_output_tokens=(
                provider_output_tokens if has_provider_output_tokens else None
            ),
            tool_schema_count_max=(
                tool_schema_count_max if has_tool_schema_metrics else None
            ),
            tool_schema_bytes_total=(
                tool_schema_bytes_total if has_tool_schema_metrics else None
            ),
            **{f"{phase}_ms": ms for phase, ms in per_phase_ms.items()},
        )


__all__ = [
    "CHAT_PHASES",
    "ChatPhase",
    "ChatPhaseTimer",
    "ChatPhaseTimingPayload",
    "active_chat_phase",
    "mark_active_chat_first_text",
    "mark_active_chat_provider_token",
    "record_active_chat_provider_call",
    "record_active_chat_provider_attempt",
    "record_chat_phase_timing_payload",
    "use_chat_phase_timer",
]


def _validate_phase_name(name: str) -> None:
    if name not in CHAT_PHASES:
        raise ValueError(
            f"Unknown chat phase: {name!r}. Allowed: {sorted(CHAT_PHASES)}"
        )


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


def mark_active_chat_provider_token() -> None:
    timer = _ACTIVE_CHAT_PHASE_TIMER.get()
    if timer is not None:
        timer.mark_provider_token()


def record_active_chat_provider_call(
    *,
    purpose: str,
    messages: list[object],
    tools: list[object],
    response: object,
) -> None:
    timer = _ACTIVE_CHAT_PHASE_TIMER.get()
    if timer is not None:
        timer.record_provider_call(
            purpose=purpose,
            messages=messages,
            tools=tools,
            response=response,
        )


def record_active_chat_provider_attempt(
    *,
    logical_call_id: str,
    semantic_purpose: str,
    attempt: int,
    provider: str,
    model: str,
    route_posture: str,
    attempt_posture: str,
    latency_ms: int,
    outcome: str,
    error_code: str = "",
) -> None:
    timer = _ACTIVE_CHAT_PHASE_TIMER.get()
    if timer is not None:
        timer.record_provider_attempt(
            logical_call_id=logical_call_id,
            semantic_purpose=semantic_purpose,
            attempt=attempt,
            provider=provider,
            model=model,
            route_posture=route_posture,
            attempt_posture=attempt_posture,
            latency_ms=latency_ms,
            outcome=outcome,
            error_code=error_code,
        )


def record_chat_phase_timing_payload(
    telemetry_service: object,
    payload: ChatPhaseTimingPayload,
) -> bool:
    record_sync = getattr(telemetry_service, "record_event_sync", None)
    if not callable(record_sync):
        return False
    from openminion.modules.telemetry.events.catalog import (  # noqa: PLC0415
        CHAT_PHASE_TIMING,
    )
    from openminion.modules.telemetry.schemas import TelemetryEvent  # noqa: PLC0415

    record_sync(
        TelemetryEvent(
            session_id=payload.session_id,
            turn_id=payload.turn_id,
            event_type=CHAT_PHASE_TIMING,
            data=payload.as_dict(),
        )
    )
    return True


def _jsonable(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _json_bytes(value: object) -> int:
    return len(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    )
