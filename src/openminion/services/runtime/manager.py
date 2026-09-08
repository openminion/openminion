from collections import OrderedDict, deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from queue import Empty, Queue
from threading import Condition, Event, RLock, Thread
from time import monotonic
from typing import Any, Callable, Iterator
from uuid import uuid4

from openminion.modules.brain.diagnostics.status import (
    phase_status_from_runtime,
    phase_status_payload,
)
from openminion.base.runtime.constants import (
    RUNTIME_TURN_STATUS_CANCELLED,
    RUNTIME_TURN_STATUS_COMPLETED,
    RUNTIME_TURN_STATUS_ERROR,
    RUNTIME_TURN_STATUS_FAILED,
    RUNTIME_TURN_STATUS_STARTED,
)
from openminion.modules.runtime.contracts import TURN_STREAM_SCHEMA_VERSION
from openminion.base.runtime.interfaces import RUNTIME_INTERFACE_VERSION
from openminion.modules.telemetry.lifecycle import (
    build_agent_runtime_component_identity,
    build_runtime_manager_component_identity,
)
from .events import emit_runtime_operation
from .constants import TURN_STREAM_HISTORY_LIMIT

from openminion.base.time import utc_now_iso as _utc_now_iso


def _new_trace_id() -> str:
    return uuid4().hex


@dataclass(frozen=True)
class ToolCallSummary:
    name: str
    count: int = 1
    status: str = "unknown"
    duration_ms: int = 0


@dataclass(frozen=True)
class TurnTelemetry:
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    retries: int = 0
    queue_wait_ms: int = 0


@dataclass(frozen=True)
class TurnError:
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnResponse:
    final_text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    tool_calls_summary: list[ToolCallSummary] = field(default_factory=list)
    memory_write_intents: list[dict[str, Any]] = field(default_factory=list)
    telemetry: TurnTelemetry = field(default_factory=TurnTelemetry)
    errors: list[TurnError] = field(default_factory=list)


@dataclass(frozen=True)
class TurnChunk:
    trace_id: str
    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=_utc_now_iso)
    schema_version: str = TURN_STREAM_SCHEMA_VERSION
    sequence: int = 0
    event_id: str = ""


@dataclass(frozen=True)
class TurnRequest:
    trace_id: str
    agent_id: str
    session_id: str
    input_text: str
    attachments: list[str] = field(default_factory=list)
    mode: str = "oneshot"
    stream: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentStatus:
    agent_id: str
    created_at: str
    last_used_at: str
    queued_turns: int
    active_turns: int
    turns_handled: int


@dataclass(frozen=True)
class AgentHandle:
    agent_id: str


TurnExecutor = Callable[[TurnRequest, Callable[[TurnChunk], None], Event], TurnResponse]
RuntimeEventHook = Callable[[str, dict[str, Any]], None]
AgentCreateHook = Callable[[str], None]
AgentEvictHook = Callable[[str, str], None]


class _ConcurrencyLimiter:
    def __init__(self, limit: int) -> None:
        self._limit = max(1, int(limit))
        self._active = 0
        self._closed = False
        self._cv = Condition(RLock())

    def acquire(self, cancel_event: Event) -> bool:
        with self._cv:
            while True:
                if self._closed or cancel_event.is_set():
                    return False
                if self._active < self._limit:
                    self._active += 1
                    return True
                self._cv.wait(timeout=0.1)

    def release(self) -> None:
        with self._cv:
            if self._active > 0:
                self._active -= 1
            self._cv.notify_all()

    def set_limit(self, limit: int) -> None:
        with self._cv:
            self._limit = max(1, int(limit))
            self._cv.notify_all()

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()


@dataclass
class _QueuedTurn:
    request: TurnRequest
    handle: "TurnHandle"
    enqueued_at_mono: float


@dataclass
class _AgentInstance:
    agent_id: str
    created_at: str = field(default_factory=_utc_now_iso)
    last_used_at: str = field(default_factory=_utc_now_iso)
    turns_handled: int = 0
    active_turns: int = 0
    active_trace_id: str = ""
    queue: Queue[_QueuedTurn | None] = field(default_factory=Queue)
    stop_event: Event = field(default_factory=Event)
    thread: Thread | None = None

    def status(self) -> AgentStatus:
        return AgentStatus(
            agent_id=self.agent_id,
            created_at=self.created_at,
            last_used_at=self.last_used_at,
            queued_turns=self.queue.qsize(),
            active_turns=self.active_turns,
            turns_handled=self.turns_handled,
        )


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
    ) -> None:
        self.trace_id = trace_id
        self.agent_id = agent_id
        self.session_id = session_id
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

    @property
    def cancel_event(self) -> Event:
        return self._cancel_event

    def cancel(self) -> bool:
        self._cancel_event.set()
        return self._on_cancel(self.trace_id)

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


class AgentRuntimeManager:
    contract_version = RUNTIME_INTERFACE_VERSION

    def __init__(
        self,
        *,
        turn_executor: TurnExecutor,
        max_agents_hot: int = 8,
        max_global_concurrency: int = 8,
        agent_ttl_seconds: int = 1800,
        sweep_interval_seconds: int = 5,
        on_runtime_event: RuntimeEventHook | None = None,
        on_agent_create: AgentCreateHook | None = None,
        on_agent_evict: AgentEvictHook | None = None,
        telemetryctl: Any | None = None,
    ) -> None:
        self._executor = turn_executor
        self._max_agents_hot = max(1, int(max_agents_hot))
        self._max_global_concurrency = max(1, int(max_global_concurrency))
        self._agent_ttl_seconds = max(1, int(agent_ttl_seconds))
        self._sweep_interval_seconds = max(1, int(sweep_interval_seconds))
        self._on_runtime_event = on_runtime_event
        self._on_agent_create = on_agent_create
        self._on_agent_evict = on_agent_evict
        self._telemetryctl = telemetryctl

        self._lock = RLock()
        self._instances: OrderedDict[str, _AgentInstance] = OrderedDict()
        self._traces: dict[str, TurnHandle] = {}
        self._limiter = _ConcurrencyLimiter(self._max_global_concurrency)
        self._stop_event = Event()
        self._started = False
        self._accepting = False
        self._stopped = False
        self._sweeper_thread: Thread | None = None
        self._lifecycle_sequence = 0

    def _emit_runtime_operation(
        self,
        *,
        session_id: str,
        turn_id: str,
        operation: str,
        status: str = "ok",
        count: int = 1,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        return emit_runtime_operation(
            telemetryctl=self._telemetryctl,
            session_id=session_id,
            turn_id=turn_id,
            operation=operation,
            status=status,
            count=count,
            extra=extra,
        )

    def start(self) -> None:
        with self._lock:
            if self._stopped:
                raise RuntimeError("runtime manager has been stopped")
            if self._started:
                return
            self._started = True
            self._accepting = True
            self._stop_event.clear()
            self._sweeper_thread = Thread(
                target=self._sweeper_loop,
                name="runtimectl-sweeper",
                daemon=True,
            )
            self._sweeper_thread.start()
        self._emit_lifecycle(
            event_type="component.started",
            component=self._runtime_manager_component(),
            reason="manager_started",
            status="ok",
            metrics={
                "max_agents_hot": self._max_agents_hot,
                "max_global_concurrency": self._max_global_concurrency,
                "sweep_interval_seconds": self._sweep_interval_seconds,
            },
        )

    def shutdown(self, grace_s: float = 10) -> None:
        with self._lock:
            if not self._started:
                return
            self._accepting = False
            self._started = False
            self._stopped = True
            self._stop_event.set()
            traces = list(self._traces.values())
            instances = list(self._instances.values())
            active_trace_count = len(self._traces)
            active_agent_count = len(self._instances)
            self._instances.clear()
        for handle in traces:
            handle.cancel()
        self._limiter.close()
        deadline = monotonic() + max(0.0, float(grace_s))
        for instance in instances:
            instance.stop_event.set()
            try:
                instance.queue.put_nowait(None)
            except Exception:
                pass
            thread = instance.thread
            if thread is not None:
                remaining = max(0.0, deadline - monotonic())
                thread.join(timeout=remaining)
        sweeper = self._sweeper_thread
        if sweeper is not None:
            sweeper.join(timeout=1.0)
        self._emit(
            "runtime.manager.shutdown",
            {"at": _utc_now_iso(), "native_lifecycle_emitted": True},
        )
        self._emit_lifecycle(
            event_type="component.stopped",
            component=self._runtime_manager_component(),
            reason="manual_stop",
            status="ok",
            metrics={
                "active_traces": active_trace_count,
                "active_agents": active_agent_count,
            },
        )

    def get_or_create_agent(self, agent_id: str) -> AgentHandle:
        normalized_agent_id = str(agent_id or "").strip()
        if not normalized_agent_id:
            raise ValueError("agent_id must be non-empty")
        with self._lock:
            instance = self._instances.get(normalized_agent_id)
            if instance is None:
                instance = _AgentInstance(agent_id=normalized_agent_id)
                instance.thread = Thread(
                    target=self._worker_loop,
                    args=(instance,),
                    name=f"runtimectl-{normalized_agent_id}",
                    daemon=True,
                )
                instance.thread.start()
                self._instances[normalized_agent_id] = instance
                self._emit(
                    "runtime.agent.created",
                    {
                        "agent_id": normalized_agent_id,
                        "created_at": instance.created_at,
                        "native_lifecycle_emitted": True,
                    },
                )
                self._emit_lifecycle(
                    event_type="component.started",
                    component=self._agent_runtime_component(normalized_agent_id),
                    reason="worker_created",
                    status="ok",
                    evidence={"created_at": instance.created_at},
                )
                if self._on_agent_create is not None:
                    self._on_agent_create(normalized_agent_id)
            self._instances.move_to_end(normalized_agent_id)
            self._evict_over_limit_locked()
        return AgentHandle(agent_id=normalized_agent_id)

    def list_agents(self) -> list[AgentStatus]:
        with self._lock:
            return [instance.status() for instance in self._instances.values()]

    def has_foreground_work(self) -> bool:
        with self._lock:
            return any(not handle._background for handle in self._traces.values())

    def get_turn_handle(self, trace_id: str) -> TurnHandle | None:
        normalized = str(trace_id or "").strip()
        if not normalized:
            return None
        with self._lock:
            return self._traces.get(normalized)

    def current_phase_status(self, trace_id: str) -> dict[str, Any] | None:
        handle = self.get_turn_handle(trace_id)
        return handle.current_phase_status() if handle is not None else None

    def evict(self, agent_id: str, reason: str) -> None:
        self._evict_agent(agent_id=agent_id, reason=reason, force=True)

    def set_limits(self, max_agents_hot: int, max_global_concurrency: int) -> None:
        with self._lock:
            self._max_agents_hot = max(1, int(max_agents_hot))
            self._max_global_concurrency = max(1, int(max_global_concurrency))
            self._limiter.set_limit(self._max_global_concurrency)
            self._evict_over_limit_locked()

    def submit_turn(self, req: TurnRequest) -> TurnHandle:
        if self._stopped:
            raise RuntimeError("runtime manager has been stopped")
        if not self._started:
            self.start()
        if not self._accepting:
            raise RuntimeError("runtime manager is not accepting new turns")

        trace_id = str(req.trace_id or "").strip() or _new_trace_id()
        request = TurnRequest(
            trace_id=trace_id,
            agent_id=str(req.agent_id or "").strip(),
            session_id=str(req.session_id or "").strip(),
            input_text=str(req.input_text or ""),
            attachments=list(req.attachments or []),
            mode=str(req.mode or "oneshot"),
            stream=bool(req.stream),
            meta=dict(req.meta or {}),
        )
        if not request.agent_id:
            raise ValueError("agent_id must be non-empty")
        if not request.session_id:
            raise ValueError("session_id must be non-empty")
        if not request.input_text.strip():
            raise ValueError("input_text must be non-empty")

        self.get_or_create_agent(request.agent_id)
        background = bool(str(request.meta.get("cron_run_id", "") or "").strip())
        handle = TurnHandle(
            trace_id=request.trace_id,
            on_cancel=self.cancel_turn,
            background=background,
            agent_id=request.agent_id,
            session_id=request.session_id,
        )
        queued = _QueuedTurn(
            request=request, handle=handle, enqueued_at_mono=monotonic()
        )
        with self._lock:
            self._traces[request.trace_id] = handle
            instance = self._instances[request.agent_id]
            instance.queue.put(queued)
            self._emit(
                "runtime.turn.enqueued",
                {
                    "trace_id": request.trace_id,
                    "agent_id": request.agent_id,
                    "session_id": request.session_id,
                    "queued_at": _utc_now_iso(),
                },
            )
        return handle

    def cancel_turn(self, trace_id: str) -> bool:
        normalized = str(trace_id or "").strip()
        if not normalized:
            return False
        with self._lock:
            handle = self._traces.get(normalized)
            if handle is None:
                return False
            handle.cancel_event.set()
        self._emit(
            "runtime.turn.cancelled",
            {"trace_id": normalized, "requested_at": _utc_now_iso()},
        )
        return True

    def kill_switch(self, grace_s: float = 2.0) -> None:
        with self._lock:
            self._accepting = False
            self._stopped = True
            traces = list(self._traces.keys())
        for trace_id in traces:
            self.cancel_turn(trace_id)
        self._emit(
            "runtime.manager.kill",
            {
                "active_traces": len(traces),
                "at": _utc_now_iso(),
                "native_lifecycle_emitted": True,
            },
        )
        self._emit_lifecycle(
            event_type="component.crashed",
            component=self._runtime_manager_component(),
            reason="kill_switch",
            status=RUNTIME_TURN_STATUS_ERROR,
            metrics={"active_traces": len(traces)},
        )
        self.shutdown(grace_s=grace_s)

    def _sweeper_loop(self) -> None:
        while not self._stop_event.is_set():
            self._sweep_once()
            if self._stop_event.wait(float(self._sweep_interval_seconds)):
                break

    def _sweep_once(self) -> None:
        now = monotonic()
        candidates: list[tuple[str, str]] = []
        with self._lock:
            active_agents = len(self._instances)
            active_traces = len(self._traces)
            for agent_id, instance in self._instances.items():
                if instance.active_turns > 0 or instance.queue.qsize() > 0:
                    continue
                age_seconds = now - _iso_to_monotonic_delta(instance.last_used_at, now)
                if age_seconds >= self._agent_ttl_seconds:
                    candidates.append((agent_id, "ttl_inactive"))
            self._evict_over_limit_locked()
        for agent_id, reason in candidates:
            self._evict_agent(agent_id=agent_id, reason=reason, force=False)
        self._emit_lifecycle(
            event_type="component.heartbeat",
            component=self._runtime_manager_component(),
            reason="heartbeat",
            status="ok",
            metrics={
                "active_agents": active_agents,
                "active_traces": active_traces,
                "sweep_interval_seconds": self._sweep_interval_seconds,
            },
        )

    def _evict_over_limit_locked(self) -> None:
        overflow = max(0, len(self._instances) - self._max_agents_hot)
        if overflow <= 0:
            return
        evicted = 0
        for agent_id, instance in list(self._instances.items()):
            if evicted >= overflow:
                break
            if instance.active_turns > 0 or instance.queue.qsize() > 0:
                continue
            evicted += 1
            self._evict_agent(agent_id=agent_id, reason="lru_over_limit", force=False)

    def _evict_agent(self, *, agent_id: str, reason: str, force: bool) -> None:
        with self._lock:
            instance = self._instances.get(agent_id)
            if instance is None:
                return
            if not force and (instance.active_turns > 0 or instance.queue.qsize() > 0):
                return
            self._instances.pop(agent_id, None)
        drained: list[_QueuedTurn] = []
        while True:
            try:
                item = instance.queue.get_nowait()
            except Empty:
                break
            if item is None:
                continue
            drained.append(item)
        for queued in drained:
            queued.handle.cancel_event.set()
            cancelled = TurnResponse(
                final_text="",
                errors=[
                    TurnError(
                        code="evicted",
                        message=f"agent evicted before execution ({reason})",
                    )
                ],
            )
            self._complete_handle(
                request=queued.request,
                handle=queued.handle,
                response=cancelled,
                runtime_status=RUNTIME_TURN_STATUS_FAILED,
            )
            with self._lock:
                self._traces.pop(queued.request.trace_id, None)
        instance.stop_event.set()
        try:
            instance.queue.put_nowait(None)
        except Exception:
            pass
        thread = instance.thread
        if thread is not None:
            thread.join(timeout=1.0)
        self._emit(
            "runtime.agent.evicted",
            {
                "agent_id": agent_id,
                "reason": reason,
                "at": _utc_now_iso(),
                "native_lifecycle_emitted": True,
            },
        )
        self._emit_lifecycle(
            event_type="component.stopped",
            component=self._agent_runtime_component(agent_id),
            reason=reason,
            status="ok",
        )
        if self._on_agent_evict is not None:
            self._on_agent_evict(agent_id, reason)

    def _worker_loop(self, instance: _AgentInstance) -> None:
        while not self._stop_event.is_set() and not instance.stop_event.is_set():
            try:
                queued = instance.queue.get(timeout=0.1)
            except Empty:
                continue
            if queued is None:
                break

            request = queued.request
            handle = queued.handle
            queue_wait_ms = max(0, int((monotonic() - queued.enqueued_at_mono) * 1000))
            self._emit(
                "runtime.turn.started",
                {
                    "trace_id": request.trace_id,
                    "agent_id": request.agent_id,
                    "session_id": request.session_id,
                    "queue_wait_ms": queue_wait_ms,
                    "started_at": _utc_now_iso(),
                },
            )
            self._emit_runtime_operation(
                session_id=request.session_id,
                turn_id=request.trace_id,
                operation="turn_start",
                extra={
                    "agent_id": request.agent_id,
                    "queue_wait_ms": queue_wait_ms,
                },
            )
            self._emit_turn_started_status(request, handle, queue_wait_ms)

            if handle.cancel_event.is_set():
                response = TurnResponse(
                    final_text="",
                    telemetry=TurnTelemetry(queue_wait_ms=queue_wait_ms),
                    errors=[
                        TurnError(
                            code=RUNTIME_TURN_STATUS_CANCELLED,
                            message="turn cancelled before execution",
                            retryable=False,
                        )
                    ],
                )
                self._complete_handle(
                    request, handle, response, RUNTIME_TURN_STATUS_CANCELLED
                )
                self._finish_turn(
                    instance=instance,
                    request=request,
                    response=response,
                    status=RUNTIME_TURN_STATUS_CANCELLED,
                )
                continue

            if not self._limiter.acquire(handle.cancel_event):
                response = TurnResponse(
                    final_text="",
                    telemetry=TurnTelemetry(queue_wait_ms=queue_wait_ms),
                    errors=[
                        TurnError(
                            code=RUNTIME_TURN_STATUS_CANCELLED,
                            message="turn cancelled during scheduling",
                            retryable=False,
                        )
                    ],
                )
                self._complete_handle(
                    request, handle, response, RUNTIME_TURN_STATUS_CANCELLED
                )
                self._finish_turn(
                    instance=instance,
                    request=request,
                    response=response,
                    status=RUNTIME_TURN_STATUS_CANCELLED,
                )
                continue

            started = monotonic()
            with self._lock:
                instance.active_turns += 1
                instance.active_trace_id = request.trace_id
            try:
                response = self._executor(
                    request, handle._push_chunk, handle.cancel_event
                )
            except Exception as exc:
                response = TurnResponse(
                    final_text="",
                    telemetry=TurnTelemetry(queue_wait_ms=queue_wait_ms),
                    errors=[
                        TurnError(code="turn_failed", message=str(exc), retryable=False)
                    ],
                )
            finally:
                self._limiter.release()
                with self._lock:
                    instance.active_turns = max(0, instance.active_turns - 1)
                    instance.active_trace_id = ""

            duration_ms = max(0, int((monotonic() - started) * 1000))
            response = replace(
                response,
                telemetry=replace(
                    response.telemetry,
                    duration_ms=duration_ms,
                    queue_wait_ms=queue_wait_ms,
                ),
            )
            status = (
                RUNTIME_TURN_STATUS_FAILED
                if response.errors
                else RUNTIME_TURN_STATUS_COMPLETED
            )
            self._complete_handle(request, handle, response, status)
            self._finish_turn(
                instance=instance, request=request, response=response, status=status
            )

        # Cancel any queued turns during shutdown.
        while True:
            try:
                leftover = instance.queue.get_nowait()
            except Empty:
                break
            if leftover is None:
                continue
            cancelled_response = TurnResponse(
                final_text="",
                errors=[
                    TurnError(
                        code=RUNTIME_TURN_STATUS_CANCELLED,
                        message="runtime shutdown",
                        retryable=False,
                    )
                ],
            )
            self._complete_handle(
                leftover.request,
                leftover.handle,
                cancelled_response,
                RUNTIME_TURN_STATUS_CANCELLED,
            )
            with self._lock:
                self._traces.pop(leftover.request.trace_id, None)

    def _finish_turn(
        self,
        *,
        instance: _AgentInstance,
        request: TurnRequest,
        response: TurnResponse,
        status: str,
    ) -> None:
        with self._lock:
            instance.turns_handled += 1
            instance.last_used_at = _utc_now_iso()
            if instance.agent_id in self._instances:
                self._instances.move_to_end(instance.agent_id)
            self._traces.pop(request.trace_id, None)
        self._emit(
            f"runtime.turn.{status}",
            {
                "trace_id": request.trace_id,
                "agent_id": request.agent_id,
                "session_id": request.session_id,
                "duration_ms": response.telemetry.duration_ms,
                "queue_wait_ms": response.telemetry.queue_wait_ms,
                "error_count": len(response.errors),
            },
        )
        if response.telemetry.retries > 0:
            self._emit_runtime_operation(
                session_id=request.session_id,
                turn_id=request.trace_id,
                operation="retry",
                count=max(1, int(response.telemetry.retries)),
                status="ok" if status == RUNTIME_TURN_STATUS_COMPLETED else "error",
                extra={
                    "agent_id": request.agent_id,
                    "runtime_status": status,
                },
            )
        self._emit_runtime_operation(
            session_id=request.session_id,
            turn_id=request.trace_id,
            operation="turn_finish",
            status="ok" if status == RUNTIME_TURN_STATUS_COMPLETED else "error",
            extra={
                "agent_id": request.agent_id,
                "runtime_status": status,
                "duration_ms": response.telemetry.duration_ms,
                "queue_wait_ms": response.telemetry.queue_wait_ms,
                "error_count": len(response.errors),
            },
        )

    @staticmethod
    def _emit_turn_started_status(
        request: TurnRequest,
        handle: TurnHandle,
        queue_wait_ms: int,
    ) -> None:
        queue_detail = (
            f"Queued for {queue_wait_ms} ms before execution."
            if queue_wait_ms > 0
            else None
        )
        status = phase_status_from_runtime(
            trace_id=request.trace_id,
            runtime_status=RUNTIME_TURN_STATUS_STARTED,
            detail_text=queue_detail,
        )
        handle._push_chunk(
            TurnChunk(
                trace_id=request.trace_id,
                kind="status",
                data=phase_status_payload(status),
            )
        )

    @staticmethod
    def _complete_handle(
        request: TurnRequest,
        handle: TurnHandle,
        response: TurnResponse,
        runtime_status: str,
    ) -> None:
        handle._push_chunk(
            TurnChunk(
                trace_id=request.trace_id,
                kind="final_text",
                data={"text": response.final_text},
            )
        )
        detail_text = response.errors[0].message if response.errors else None
        handle._push_chunk(
            TurnChunk(
                trace_id=request.trace_id,
                kind="status",
                data=phase_status_payload(
                    phase_status_from_runtime(
                        trace_id=request.trace_id,
                        runtime_status=runtime_status,
                        detail_text=detail_text,
                        terminal=True,
                    )
                ),
            )
        )
        handle._set_result(response)

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._on_runtime_event is None:
            return
        event_payload = dict(payload)
        event_payload.setdefault("ts", _utc_now_iso())
        self._on_runtime_event(event_type, event_payload)

    def _emit_lifecycle(
        self,
        *,
        event_type: str,
        component: dict[str, Any],
        reason: str,
        status: str,
        metrics: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        component_kind = str(component.get("component_kind") or "").strip()
        component_id = str(component.get("component_id") or "").strip()
        if not component_kind or not component_id:
            return
        self._lifecycle_sequence += 1
        self._emit(
            event_type,
            {
                "component": dict(component),
                "module_id": "openminion-runtime",
                "session_id": f"lifecycle:{component_kind}:{component_id}",
                "turn_id": (
                    f"{component_kind}:{component_id}:{event_type.rsplit('.', 1)[-1]}:{self._lifecycle_sequence}"
                ),
                "reason": reason,
                "status": status,
                "metrics": dict(metrics or {}),
                "evidence": dict(evidence or {}),
                "source_classification": "native_canonical",
            },
        )

    def _runtime_manager_component(self) -> dict[str, Any]:
        return build_runtime_manager_component_identity()

    def _agent_runtime_component(self, agent_id: str) -> dict[str, Any]:
        return build_agent_runtime_component_identity(agent_id.strip())


def _iso_to_monotonic_delta(ts: str, now_mono: float) -> float:
    try:
        value = datetime.fromisoformat(ts)
    except ValueError:
        return now_mono
    now_utc = datetime.now(timezone.utc)
    delta = (now_utc - value).total_seconds()
    return max(0.0, now_mono - delta)
