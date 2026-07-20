import json
from dataclasses import asdict
from functools import partial
import hashlib
from time import perf_counter
from typing import Any

from openminion.services.runtime.manager import (
    AgentRuntimeManager,
    ToolCallSummary,
    TurnChunk,
    TurnError,
    TurnResponse,
    TurnTelemetry,
)
from openminion.modules.telemetry.lifecycle import (
    lifecycle_event_from_payload,
    map_cron_event_to_lifecycle_event,
    map_runtime_event_to_lifecycle_event,
)
from openminion.modules.telemetry.service import TelemetryService
from openminion.modules.telemetry.trace import phase_timing
from openminion.base.logging import format_structured_event, get_logger
from openminion.services.runtime.ingress import (
    _emit_chat_phase_timing,
    build_manager_turn_request,
    execute_runtime_turn,
    runtime_turn_request_from_manager_request,
    TurnRequestError,
    TurnTimeoutError,
)
from openminion.services.runtime.cron.delivery import CronDeliveryBridge
from openminion.services.runtime.cron.executor import CronTurnExecutor

if False:  # pragma: no cover
    from openminion.services.runtime.interfaces import RuntimeFacade


_DAEMON_LOGGER = get_logger("daemon")
_CRON_LOGGER = get_logger("modules.cron")
_LIFECYCLE_LOGGER = get_logger("lifecycle")
_RUNTIMECTL_LOGGER = get_logger("runtimectl")
_CRONCTL_LOGGER = get_logger("cronctl")


class _LifecycleTelemetryBridge:
    def __init__(self, runtime: "RuntimeFacade") -> None:
        self._runtime = runtime
        # reuse the runtime's pre-built TelemetryService so the
        existing = getattr(runtime, "telemetry_service", None)
        if existing is not None:
            self._telemetry = existing
            self._owns_telemetry = False
        else:
            self._telemetry = TelemetryService(
                home_root=runtime.home_root,
                env=getattr(runtime.config.runtime, "env", None),
                otel_exporter_config=getattr(
                    runtime.config.runtime,
                    "telemetry_exporter",
                    None,
                ),
            )
            self._owns_telemetry = True
        self._logger = _LIFECYCLE_LOGGER

    @property
    def telemetry(self) -> TelemetryService:
        return self._telemetry

    def close(self) -> None:
        if not self._owns_telemetry:
            return
        try:
            self._telemetry.close_sync()
        except Exception:
            return

    def handle_runtime_event(self, event_type: str, payload: dict[str, Any]) -> None:
        canonical = lifecycle_event_from_payload(event_type, payload)
        if canonical is None:
            canonical = map_runtime_event_to_lifecycle_event(event_type, payload)
        if canonical is not None:
            self._record(canonical)
            return
        _RUNTIMECTL_LOGGER.info(
            format_structured_event(
                event_type,
                payload=json.dumps(payload, sort_keys=True),
            )
        )

    def handle_cron_event(self, event_type: str, payload: dict[str, Any]) -> None:
        canonical = map_cron_event_to_lifecycle_event(event_type, payload)
        if canonical is not None:
            self._record(canonical)
            return
        _CRONCTL_LOGGER.info(
            format_structured_event(
                event_type,
                payload=json.dumps(payload, sort_keys=True),
            )
        )

    def _record(self, event: Any) -> None:
        self._telemetry.record_event_sync(event)
        self._logger.info(
            format_structured_event(
                event.event_type,
                source=event.data.get("source_event_type", ""),
                component=json.dumps(event.data.get("component", {}), sort_keys=True),
            )
        )


def build_runtime_manager(runtime: "RuntimeFacade") -> Any:
    lifecycle_bridge = _LifecycleTelemetryBridge(runtime)
    runtime._lifecycle_event_bridge = lifecycle_bridge

    def _on_agent_evict(agent_id: str, reason: str) -> None:
        runtime.evict_agent_runtime(agent_id=agent_id, reason=reason)

    manager = AgentRuntimeManager(
        turn_executor=lambda req, emit_chunk, cancel_event: execute_turn(
            runtime=runtime,
            request=req,
            emit_chunk=emit_chunk,
            cancel_event=cancel_event,
        ),
        max_agents_hot=8,
        max_global_concurrency=8,
        agent_ttl_seconds=30 * 60,
        sweep_interval_seconds=5,
        on_runtime_event=lifecycle_bridge.handle_runtime_event,
        on_agent_evict=_on_agent_evict,
    )
    manager.start()
    return manager


def build_turn_request(payload: dict[str, Any], *, default_agent_id: str) -> Any:
    return build_manager_turn_request(
        payload,
        default_agent_id=default_agent_id,
    )


def attach_cron_scheduler(
    *,
    runtime: "RuntimeFacade",
    daemon_id: str,
    daemon_component_id: str = "primary",
    daemon_pid: int | None = None,
    tick_seconds: float = 0.5,
    lease_ttl_seconds: int = 60,
    max_concurrent_runs: int = 5,
) -> Any:
    """Attach the cron scheduler to a runtime instance.

    Returns the scheduler instance or None if initialization fails.
    """
    try:
        from openminion.modules.storage.runtime.sqlite import resolve_database_path
        from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
        from openminion.services.cron.scheduler import CronScheduler
        from openminion.modules.brain.paths import resolve_brain_sessions_db_path
    except Exception as exc:  # noqa: BLE001
        _DAEMON_LOGGER.warning(
            format_structured_event(
                "cron.scheduler.import_failed",
                error=exc,
            )
        )
        return None

    try:
        lifecycle_bridge = getattr(runtime, "_lifecycle_event_bridge", None)
        storage_path = resolve_database_path(
            runtime.config.storage.path,
            env=getattr(runtime.config.runtime, "env", None),
        )
        db_path = resolve_brain_sessions_db_path(storage_path=storage_path)
        cron_store = SQLiteSessionStore(db_path)

        cron_timeout_s: float = max(
            10.0,
            float(
                getattr(runtime.config.runtime, "chat_turn_timeout_seconds", 0) or 90.0
            ),
        )
        cron_max_attempts: int = max(
            1,
            int(getattr(runtime.config.runtime, "chat_turn_max_attempts", 0) or 2),
        )
        turn_executor = CronTurnExecutor(
            runtime=runtime,
            cron_store=cron_store,
            request_builder=lambda payload, agent_id: build_turn_request(
                payload,
                default_agent_id=agent_id,
            ),
            timeout_s=cron_timeout_s,
            max_attempts=cron_max_attempts,
        )
        delivery_bridge = CronDeliveryBridge(runtime=runtime)

        scheduler = CronScheduler(
            store=cron_store,
            daemon_id=daemon_id,
            daemon_component_id=daemon_component_id,
            daemon_pid=daemon_pid,
            tick_seconds=tick_seconds,
            lease_ttl_seconds=lease_ttl_seconds,
            max_concurrent_runs=max_concurrent_runs,
            execute_agent_turn=turn_executor.execute,
            delivery_handler=delivery_bridge.deliver,
            on_event=(
                lifecycle_bridge.handle_cron_event
                if lifecycle_bridge is not None
                else None
            ),
        )
        scheduler.start()

        try:
            cron_store.add_cron_job(
                name="system-cron-cleanup",
                description="Prune old cron history",
                schedule={"kind": "every", "every_ms": 86_400_000},
                payload={
                    "kind": "systemEvent",
                    "event_text": "prune_cron_runs",
                    "kwargs": {"days": 7},
                },
                agent_id="system",
                session_target="main",
                wake_mode="none",
                delete_after_run=False,
                job_id="system-cron-cleanup",
            )
        except Exception as exc:  # noqa: BLE001
            _DAEMON_LOGGER.warning(
                format_structured_event(
                    "cron.cleanup.seed_failed",
                    error=exc,
                )
            )

        return scheduler
    except Exception as exc:  # noqa: BLE001
        _DAEMON_LOGGER.warning(
            format_structured_event(
                "cron.scheduler.start_failed",
                error=exc,
            )
        )
        return None


def execute_turn(
    *,
    runtime: "RuntimeFacade",
    request: Any,
    emit_chunk: Any,
    cancel_event: Any,
) -> Any:
    started = perf_counter()
    if cancel_event.is_set():
        return TurnResponse(
            final_text="",
            errors=[
                TurnError(
                    code="cancelled",
                    message="turn cancelled before execution",
                    retryable=False,
                )
            ],
            stats={},
            telemetry=TurnTelemetry(duration_ms=0),
        )

    emit_phase_status = partial(
        _emit_turn_phase_status,
        request=request,
        cancel_event=cancel_event,
        emit_chunk=emit_chunk,
    )
    timer = phase_timing.ChatPhaseTimer(cold_start=bool(request.meta.get("cold_start")))
    ingress_request = None
    try:
        with phase_timing.use_chat_phase_timer(timer):
            with phase_timing.active_chat_phase("provider_request_build"):
                ingress_request = runtime_turn_request_from_manager_request(
                    runtime=runtime,
                    request=request,
                )
            turn_result = execute_runtime_turn(
                runtime=runtime,
                request=ingress_request,
                progress_callback=emit_phase_status,
            )
    except TurnRequestError as exc:
        duration_ms = max(0, int((perf_counter() - started) * 1000))
        return TurnResponse(
            final_text="",
            metadata={},
            stats={},
            errors=[
                TurnError(code="invalid_request", message=str(exc), retryable=False)
            ],
            telemetry=TurnTelemetry(duration_ms=duration_ms),
        )
    except TurnTimeoutError as exc:
        duration_ms = max(0, int((perf_counter() - started) * 1000))
        return TurnResponse(
            final_text="",
            metadata={},
            stats={},
            errors=[TurnError(code="turn_timeout", message=str(exc), retryable=True)],
            telemetry=TurnTelemetry(duration_ms=duration_ms),
        )
    except Exception as exc:
        duration_ms = max(0, int((perf_counter() - started) * 1000))
        return TurnResponse(
            final_text="",
            metadata={},
            stats={},
            errors=[TurnError(code="turn_failed", message=str(exc), retryable=False)],
            telemetry=TurnTelemetry(duration_ms=duration_ms),
        )
    finally:
        if ingress_request is not None:
            _emit_chat_phase_timing(
                runtime=runtime,
                timer=timer,
                request=ingress_request,
            )

    metadata = dict(turn_result.metadata)
    tool_calls = _tool_call_summaries(metadata.get("tool_calls"))
    artifact_refs = _tool_artifact_refs(
        metadata.get("tool_results"),
        session_id=request.session_id,
        trace_id=request.trace_id,
    )
    duration_ms = max(0, int((perf_counter() - started) * 1000))
    return TurnResponse(
        final_text=turn_result.body,
        metadata=metadata,
        stats=turn_result.stats.as_payload()
        if turn_result.stats is not None and turn_result.stats.has_any_data
        else {},
        artifacts=artifact_refs,
        tool_calls_summary=tool_calls,
        memory_write_intents=[],
        telemetry=TurnTelemetry(
            tokens_in=_to_int(metadata.get("usage_prompt_tokens")),
            tokens_out=_to_int(metadata.get("usage_completion_tokens")),
            duration_ms=duration_ms,
            retries=0,
            queue_wait_ms=0,
        ),
        errors=[],
    )


def _emit_turn_phase_status(
    status: object,
    *,
    request: Any,
    cancel_event: Any,
    emit_chunk: Any,
) -> None:
    if cancel_event.is_set():
        return
    chunk_kind = "status"
    if hasattr(status, "model_dump"):
        try:
            status_payload = status.model_dump()
        except Exception:
            return
    elif isinstance(status, dict):
        status_payload = dict(status)
    else:
        return
    candidate_kind = str(status_payload.get("kind", "") or "").strip()
    if candidate_kind in {"tool_started", "tool_completed", "budget_event"}:
        chunk_kind = candidate_kind
    emit_chunk(
        TurnChunk(
            trace_id=request.trace_id,
            kind=chunk_kind,
            data=status_payload,
        )
    )


def turn_response_to_dict(response: Any) -> dict[str, Any]:
    return asdict(response)


def turn_chunk_to_dict(chunk: Any) -> dict[str, Any]:
    return asdict(chunk)


def agent_status_to_dict(status: Any) -> dict[str, Any]:
    return asdict(status)


def _tool_call_summaries(raw: Any) -> list[Any]:
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    counts: dict[str, int] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1
    out: list[Any] = []
    for name, count in sorted(counts.items()):
        out.append(
            ToolCallSummary(name=name, count=count, status="success", duration_ms=0)
        )
    return out


def _tool_artifact_refs(
    raw: Any, *, session_id: str, trace_id: str
) -> list[dict[str, Any]]:
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    refs: list[dict[str, Any]] = []
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name", "")).strip() or "tool"
        artifact_refs = item.get("artifact_refs")
        emitted = False
        if isinstance(artifact_refs, list):
            seen: set[str] = set()
            for candidate in artifact_refs:
                ref = ""
                if isinstance(candidate, dict):
                    ref = str(candidate.get("ref", "") or "").strip()
                else:
                    ref = str(candidate or "").strip()
                if not ref or ref in seen:
                    continue
                seen.add(ref)
                emitted = True
                refs.append(
                    {
                        "ref": ref,
                        "type": "tool_result",
                        "tool": tool_name,
                    }
                )
        if emitted:
            continue
        digest = hashlib.sha1(
            json.dumps(item, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        refs.append(
            {
                "ref": f"artifact://tool/{session_id}/{trace_id}/{index}-{tool_name}-{digest}",
                "type": "tool_result",
                "tool": tool_name,
            }
        )
    return refs


def _to_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)
