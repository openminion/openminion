from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from openminion.base.config import OTELExporterConfig
from ..schemas import TelemetryEvent
from .attributes import (
    agent_span_name as _agent_span_name,
    attributes_for_event as _attributes_for_event,
    model_span_name as _model_span_name,
    span_kind_for_event as _span_kind_for_event,
    tool_span_name as _tool_span_name,
)
from .performance_metrics import (
    generic_metric_projection,
    performance_metrics_for_event,
)
from .queueing import NoncriticalExportQueue
from .logs import log_projection_for_event
from .sdk import (
    ExportedOTELRecord,
    OpenTelemetrySDKSink as _OpenTelemetrySDKSink,
    OTELTraceSink,
    RecordingOTELTraceSink,
    create_otel_trace_sink,
)
from ..interfaces import TelemetryExportProbeResult

_LOG = logging.getLogger(__name__)
_TERMINAL_EVENT_PREFIXES = (
    "turn.assistant",
    "turn.tool",
    "turn.system",
)
_TERMINAL_EVENT_TYPES = frozenset({"component.crashed", "component.stopped"})

_CLASS_SPAN = "span"
_CLASS_METRIC = "metric"
_CLASS_LOG = "log_record"
_CLASS_EXCLUDED = "excluded"
OTEL_SEMCONV_VERSION = "1.44.0"
OTEL_GENAI_SEMCONV_COMMIT = "46d43c8949afb53765a202e89f4534eeb75ca3fa"

_EVENT_CLASSIFICATION: dict[str, str] = {
    "storage.query": _CLASS_SPAN,
    "storage.slow_query": _CLASS_SPAN,
    "storage.migration": _CLASS_SPAN,
    "storage.pool.stats": _CLASS_METRIC,
    "memory.scope_capacity.evicted": _CLASS_METRIC,
    "memory.soft_deleted.purged": _CLASS_METRIC,
    "llm.call.completed": _CLASS_SPAN,
    # LLM cache metrics — point-in-time hit/miss observation. Treat
    # as a gauge; its source semantics are point-in-time rather than cumulative.
    "llm.cache.metrics": _CLASS_METRIC,
    "chat.phase_timing": _CLASS_SPAN,
    "module.stats": _CLASS_METRIC,
    "tui.render": _CLASS_METRIC,
    "telemetry.queue.stats": _CLASS_METRIC,
    "telemetry.export.probe": _CLASS_LOG,
    # Generic catchalls stay out of OTel emission; module.debug.failure remains
    # a log record so runtime failure diagnostics are still visible.
    "metric": _CLASS_EXCLUDED,
    "message": _CLASS_EXCLUDED,
    "module.debug.failure": _CLASS_LOG,
}

_PAIRED_SPAN_CLASSES: dict[str, tuple[str, tuple[str, ...], str]] = {
    "llm.call.started": (
        "llm.call.completed",
        ("llm_call_id", "call_id", "request_id"),
        "llm.call",
    ),
    "rlm.tick.started": ("rlm.tick.completed", ("tick_id", "tick_index"), "rlm.tick"),
    "tool.execution.started": (
        "tool.execution.completed",
        ("tool_call_id", "call_id"),
        "execute_tool",
    ),
    "agent.execution.started": (
        "agent.execution.completed",
        ("execution_id",),
        "invoke_agent",
    ),
    "agent.turn.started": (
        "agent.turn.completed",
        ("turn_operation_id", "turn_id"),
        "openminion.turn",
    ),
    "agent.phase.started": (
        "agent.phase.completed",
        ("phase_id",),
        "openminion.phase",
    ),
    "agent.handoff.started": (
        "agent.handoff.completed",
        ("handoff_id",),
        "invoke_agent",
    ),
}
_PAIRED_COMPLETION_EVENTS: dict[str, tuple[str, tuple[str, ...]]] = {
    completion: (start, pairing_keys)
    for start, (completion, pairing_keys, _) in _PAIRED_SPAN_CLASSES.items()
}
_PAIRED_COMPLETION_EVENTS["llm.call.failed"] = (
    "llm.call.started",
    ("llm_call_id", "call_id", "request_id"),
)
_PAIRED_COMPLETION_EVENTS["tool.execution.failed"] = (
    "tool.execution.started",
    ("tool_call_id", "call_id"),
)
for _terminal, _started, _keys in (
    ("agent.execution.paused", "agent.execution.started", ("execution_id",)),
    ("agent.execution.failed", "agent.execution.started", ("execution_id",)),
    ("agent.execution.cancelled", "agent.execution.started", ("execution_id",)),
    ("agent.turn.failed", "agent.turn.started", ("turn_operation_id", "turn_id")),
    ("agent.phase.failed", "agent.phase.started", ("phase_id",)),
    ("agent.handoff.failed", "agent.handoff.started", ("handoff_id",)),
):
    _PAIRED_COMPLETION_EVENTS[_terminal] = (_started, _keys)


class OpenTelemetryTraceExporter:
    def __init__(
        self,
        config: OTELExporterConfig | None,
        *,
        sink: OTELTraceSink | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config or OTELExporterConfig()
        self._logger = logger or _LOG
        endpoint = str(self._config.endpoint or "").strip()
        if not bool(self._config.enabled) or not endpoint:
            self._sink = None
        else:
            self._sink = sink or create_otel_trace_sink(
                self._config,
                logger=self._logger,
            )
        self._pending_paired_spans: dict[str, dict[str, Any]] = {}
        self._deferred_spans: dict[str, list[dict[str, Any]]] = {}
        self._deferred_events: dict[str, list[dict[str, Any]]] = {}
        self._deferred_logs: dict[str, list[dict[str, Any]]] = {}
        self._export_queue = (
            NoncriticalExportQueue(
                capacity=int(self._config.noncritical_queue_capacity),
                flush_timeout_seconds=float(self._config.queue_flush_timeout_seconds),
                export_now=self._export_now,
            )
            if self._sink is not None
            else None
        )

    _MAX_PENDING_PAIRED_SPANS = 1024

    @property
    def enabled(self) -> bool:
        return self._sink is not None and bool(self._config.enabled)

    def export(self, event: TelemetryEvent) -> bool:
        if self._sink is None:
            return False
        trace_key = _trace_key_for_event(event)
        if not _is_sampled(trace_key, self._config.sample_rate):
            return False
        event_type = str(event.event_type or "").strip()
        if _EVENT_CLASSIFICATION.get(event_type) == _CLASS_EXCLUDED:
            return False
        if self._export_queue is not None and self._export_queue.should_queue(event):
            return self._export_queue.enqueue(event)
        return self._export_now(event, trace_key=trace_key, event_type=event_type)

    def queue_stats(self) -> dict[str, int]:
        if self._export_queue is None:
            return {
                "queue_capacity": 0,
                "queue_depth": 0,
                "drops": 0,
                "flush_failures": 0,
            }
        return self._export_queue.stats()

    def delete_pending_invocation(self, invocation_id: str) -> int:
        if self._export_queue is None:
            return 0
        return self._export_queue.delete_pending_invocation(invocation_id)

    def probe(
        self,
        event: TelemetryEvent,
        timeout_seconds: float,
    ) -> TelemetryExportProbeResult:
        if self._sink is None:
            return TelemetryExportProbeResult(True, "rejected", "not_run")
        started = time.monotonic()
        attributes = {
            "openminion.event_type": str(event.event_type),
            "openminion.telemetry.probe": True,
            "openminion.payload.criticality": "diagnostic",
            "openminion.payload.protocol": str(event.data.get("protocol") or ""),
        }
        projection = log_projection_for_event(event, attributes=attributes)
        if projection is None:
            return TelemetryExportProbeResult(True, "rejected", "not_run")
        try:
            self._sink.emit_log(
                trace_key=_trace_key_for_event(event),
                session_id=event.session_id,
                turn_id=event.turn_id,
                record_type=projection.record_type,
                event_name=projection.event_name,
                severity=projection.severity,
                body=projection.body,
                attributes=projection.attributes,
                timestamp_ns=_timestamp_ns(event.timestamp),
            )
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                return TelemetryExportProbeResult(True, "timeout", "not_run")
            flushed = self._sink.force_flush(remaining)
        except TimeoutError:
            return TelemetryExportProbeResult(True, "timeout", "not_run")
        except Exception:  # noqa: BLE001
            return TelemetryExportProbeResult(True, "failed", "not_run")
        if not flushed:
            return TelemetryExportProbeResult(True, "accepted", "failed")
        return TelemetryExportProbeResult(
            True,
            "accepted",
            "completed",
            recording_sink=isinstance(self._sink, RecordingOTELTraceSink),
        )

    def _export_now(
        self,
        event: TelemetryEvent,
        *,
        trace_key: str | None = None,
        event_type: str | None = None,
    ) -> bool:
        if self._sink is None:
            return False
        trace_key = trace_key or _trace_key_for_event(event)
        event_type = event_type or str(event.event_type or "").strip()
        timestamp_ns = _timestamp_ns(event.timestamp)
        attributes = _attributes_for_event(
            event,
            include_assistant_body=bool(self._config.include_assistant_body),
        )
        try:
            if event_type in _PAIRED_SPAN_CLASSES:
                self._capture_paired_start(
                    event_type=event_type,
                    event=event,
                    attributes=attributes,
                    timestamp_ns=timestamp_ns,
                    trace_key=trace_key,
                )
                self._emit_log_projection(
                    event=event,
                    attributes=attributes,
                    timestamp_ns=timestamp_ns,
                    trace_key=trace_key,
                )
                return True
            if event_type in _PAIRED_COMPLETION_EVENTS:
                if self._emit_paired_completion(
                    event_type=event_type,
                    event=event,
                    attributes=attributes,
                    timestamp_ns=timestamp_ns,
                    trace_key=trace_key,
                ):
                    self._emit_log_projection(
                        event=event,
                        attributes=attributes,
                        timestamp_ns=timestamp_ns,
                        trace_key=trace_key,
                    )
                    self._emit_performance_metrics(
                        event=event,
                        timestamp_ns=timestamp_ns,
                        trace_key=trace_key,
                    )
                    return True
            if self._emit_log_projection(
                event=event,
                attributes=attributes,
                timestamp_ns=timestamp_ns,
                trace_key=trace_key,
            ):
                self._emit_performance_metrics(
                    event=event,
                    timestamp_ns=timestamp_ns,
                    trace_key=trace_key,
                )
                return True
            self._emit_classified_projection(
                event=event,
                event_type=event_type,
                trace_key=trace_key,
                timestamp_ns=timestamp_ns,
                attributes=attributes,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "OpenTelemetry export failed for event_type=%s: %s",
                event.event_type,
                exc,
            )
            return False

    def _emit_classified_projection(
        self,
        *,
        event: TelemetryEvent,
        event_type: str,
        trace_key: str,
        timestamp_ns: int,
        attributes: dict[str, Any],
    ) -> None:
        assert self._sink is not None
        classification = _EVENT_CLASSIFICATION.get(event_type)
        if classification is None and event_type.startswith("tool."):
            classification = _CLASS_SPAN
        if classification == _CLASS_SPAN:
            self._emit_or_defer_span(
                event=event,
                trace_key=trace_key,
                session_id=event.session_id,
                turn_id=event.turn_id,
                span_name=event_type or "openminion.event",
                attributes=attributes,
                timestamp_ns=timestamp_ns,
                span_key=f"event:{event_type}:{timestamp_ns}",
                parent_span_key=_parent_span_key_for_event(event),
            )
        elif classification == _CLASS_METRIC:
            metric_kind, unit, value = generic_metric_projection(event)
            self._sink.emit_metric(
                trace_key=trace_key,
                session_id=event.session_id,
                turn_id=event.turn_id,
                metric_name=event_type or "openminion.event",
                metric_kind=metric_kind,
                unit=unit,
                value=value,
                attributes=attributes,
                timestamp_ns=timestamp_ns,
            )
        else:
            self._emit_or_defer_event(
                event=event,
                trace_key=trace_key,
                session_id=event.session_id,
                turn_id=event.turn_id,
                event_name=event_type or "event",
                attributes=attributes,
                timestamp_ns=timestamp_ns,
                terminal=_is_terminal_event(event.event_type),
                parent_span_key=_parent_span_key_for_event(event),
            )
        self._emit_performance_metrics(
            event=event,
            timestamp_ns=timestamp_ns,
            trace_key=trace_key,
        )

    def _emit_log_projection(
        self,
        *,
        event: TelemetryEvent,
        attributes: dict[str, Any],
        timestamp_ns: int,
        trace_key: str,
    ) -> bool:
        if self._sink is None:
            return False
        projection = log_projection_for_event(event, attributes=attributes)
        if projection is None:
            return False
        self._emit_or_defer_log(
            event=event,
            trace_key=trace_key,
            session_id=event.session_id,
            turn_id=event.turn_id,
            record_type=projection.record_type,
            event_name=projection.event_name,
            severity=projection.severity,
            body=projection.body,
            attributes=projection.attributes,
            timestamp_ns=timestamp_ns,
            parent_span_key=_parent_span_key_for_event(event),
        )
        return True

    def _pending_execution_id(self, event: TelemetryEvent) -> str:
        execution_id = _execution_id_for_event(event)
        if not execution_id:
            return ""
        slot = f"agent.execution.started:{execution_id}"
        return execution_id if slot in self._pending_paired_spans else ""

    def _emit_or_defer_span(
        self,
        *,
        event: TelemetryEvent,
        execution_id: str = "",
        **span: Any,
    ) -> None:
        pending_execution_id = execution_id or self._pending_execution_id(event)
        if pending_execution_id:
            self._deferred_spans.setdefault(pending_execution_id, []).append(span)
            return
        assert self._sink is not None
        self._sink.emit_span(**span)

    def _emit_or_defer_event(
        self,
        *,
        event: TelemetryEvent,
        **record: Any,
    ) -> None:
        execution_id = self._pending_execution_id(event)
        if execution_id:
            self._deferred_events.setdefault(execution_id, []).append(record)
            return
        assert self._sink is not None
        self._sink.emit_event(**record)

    def _emit_or_defer_log(
        self,
        *,
        event: TelemetryEvent,
        **record: Any,
    ) -> None:
        execution_id = self._pending_execution_id(event)
        if execution_id:
            self._deferred_logs.setdefault(execution_id, []).append(record)
            return
        assert self._sink is not None
        self._sink.emit_log(**record)

    def _flush_execution(self, execution_id: str, trace_key: str) -> None:
        assert self._sink is not None
        spans = self._deferred_spans.pop(execution_id, [])
        trace_keys = {trace_key}
        trace_keys.update(str(span.get("trace_key") or "") for span in spans)
        spans.sort(key=lambda item: _span_depth(str(item.get("span_key") or "")))
        for span in spans:
            self._sink.emit_span(**span)
        events = self._deferred_events.pop(execution_id, [])
        logs = self._deferred_logs.pop(execution_id, [])
        trace_keys.update(str(record.get("trace_key") or "") for record in events)
        trace_keys.update(str(record.get("trace_key") or "") for record in logs)
        for record in events:
            self._sink.emit_event(**record)
        for record in logs:
            self._sink.emit_log(**record)
        for trace_key in trace_keys:
            if trace_key:
                self._sink.release_trace(trace_key)

    def _emit_performance_metrics(
        self,
        *,
        event: TelemetryEvent,
        timestamp_ns: int,
        trace_key: str,
    ) -> None:
        if self._sink is None:
            return
        for metric in performance_metrics_for_event(event):
            self._sink.emit_metric(
                trace_key=trace_key,
                session_id=event.session_id,
                turn_id=event.turn_id,
                metric_name=metric["name"],
                metric_kind=metric["kind"],
                unit=str(metric["unit"]),
                value=float(metric["value"]),
                attributes=dict(metric["attributes"]),
                timestamp_ns=timestamp_ns,
            )

    def _capture_paired_start(
        self,
        *,
        event_type: str,
        event: TelemetryEvent,
        attributes: dict[str, Any],
        timestamp_ns: int,
        trace_key: str,
    ) -> None:
        pairing_keys = _PAIRED_SPAN_CLASSES[event_type][1]
        pairing_id = _resolve_pairing_id(event, pairing_keys)
        if not pairing_id:
            self._emit_or_defer_event(
                event=event,
                trace_key=trace_key,
                session_id=event.session_id,
                turn_id=event.turn_id,
                event_name=event_type,
                attributes=attributes,
                timestamp_ns=timestamp_ns,
                terminal=False,
                parent_span_key=_parent_span_key_for_event(event),
            )
            return
        slot = f"{event_type}:{pairing_id}"
        span_key, parent_span_key = _span_keys(event_type, event, pairing_id)
        if (
            slot not in self._pending_paired_spans
            and len(self._pending_paired_spans) >= self._MAX_PENDING_PAIRED_SPANS
        ):
            oldest = next(iter(self._pending_paired_spans))
            self._pending_paired_spans.pop(oldest, None)
        self._pending_paired_spans[slot] = {
            "trace_key": trace_key,
            "session_id": event.session_id,
            "turn_id": event.turn_id,
            "attributes": dict(attributes),
            "start_timestamp_ns": timestamp_ns,
            "span_name": (
                _model_span_name(event)
                if event_type == "llm.call.started"
                else _tool_span_name(event)
                if event_type == "tool.execution.started"
                else _agent_span_name(event)
                if event_type.startswith("agent.")
                else _PAIRED_SPAN_CLASSES[event_type][2]
            ),
            "span_kind": _span_kind_for_event(event),
            "parent_traceparent": str((event.data or {}).get("traceparent") or ""),
            "tracestate": str((event.data or {}).get("tracestate") or ""),
            "span_links": tuple((event.data or {}).get("span_links") or ()),
            "span_key": span_key,
            "parent_span_key": parent_span_key,
        }

    def _emit_paired_completion(
        self,
        *,
        event_type: str,
        event: TelemetryEvent,
        attributes: dict[str, Any],
        timestamp_ns: int,
        trace_key: str,
    ) -> bool:
        assert self._sink is not None
        start_event_type, pairing_keys = _PAIRED_COMPLETION_EVENTS[event_type]
        pairing_id = _resolve_pairing_id(event, pairing_keys)
        if not pairing_id:
            return False
        slot = f"{start_event_type}:{pairing_id}"
        pending = self._pending_paired_spans.pop(slot, None)
        if pending is None:
            return False
        merged_attributes = dict(pending["attributes"])
        merged_attributes.update(attributes)
        span: dict[str, Any] = {
            "trace_key": trace_key,
            "session_id": event.session_id,
            "turn_id": event.turn_id,
            "span_name": str(pending["span_name"]),
            "attributes": merged_attributes,
            "timestamp_ns": int(pending["start_timestamp_ns"]),
            "end_timestamp_ns": timestamp_ns,
            "span_kind": str(pending["span_kind"]),
            "parent_traceparent": str(pending["parent_traceparent"]),
            "tracestate": str(pending["tracestate"]),
            "span_links": tuple(pending["span_links"]),
            "span_key": str(pending["span_key"]),
            "parent_span_key": str(pending["parent_span_key"]),
        }
        execution_id = _execution_id_for_event(event)
        if start_event_type == "agent.execution.started":
            self._sink.emit_span(**span)
            if execution_id:
                self._flush_execution(execution_id, str(span["trace_key"]))
        else:
            self._emit_or_defer_span(
                event=event,
                **span,
            )
        return True

    def close(self) -> None:
        if self._sink is None:
            return
        if self._export_queue is not None:
            self._export_queue.close()
        try:
            self._sink.close()
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("OpenTelemetry exporter shutdown failed: %s", exc)
        finally:
            self._sink = None
            self._export_queue = None
            self._pending_paired_spans.clear()
            self._deferred_spans.clear()
            self._deferred_events.clear()
            self._deferred_logs.clear()


def _execution_span_key(event: TelemetryEvent) -> str:
    execution_id = _execution_id_for_event(event)
    return f"execution:{execution_id}" if execution_id else ""


def _turn_span_key(event: TelemetryEvent) -> str:
    execution_id = _execution_id_for_event(event)
    turn_id = str(event.turn_id or "")
    return f"turn:{execution_id}:{turn_id}" if execution_id and turn_id else ""


def _execution_id_for_event(event: TelemetryEvent) -> str:
    return str(event.execution_id or event.data.get("execution_id") or "")


def _span_keys(
    start_event_type: str,
    event: TelemetryEvent,
    pairing_id: str,
) -> tuple[str, str]:
    if start_event_type == "agent.execution.started":
        return _execution_span_key(event), ""
    if start_event_type == "agent.turn.started":
        return _turn_span_key(event), _execution_span_key(event)
    if start_event_type == "agent.phase.started":
        return f"phase:{pairing_id}", _turn_span_key(event)
    if start_event_type == "llm.call.started":
        return f"llm:{pairing_id}", _turn_span_key(event)
    if start_event_type == "tool.execution.started":
        return f"tool:{pairing_id}", _turn_span_key(event)
    if start_event_type == "agent.handoff.started":
        return f"handoff:{pairing_id}", _turn_span_key(event)
    return f"operation:{start_event_type}:{pairing_id}", _turn_span_key(event)


def _parent_span_key_for_event(event: TelemetryEvent) -> str:
    event_type = str(event.event_type or "")
    payload = event.data
    if event_type.startswith("tool.execution."):
        pairing_id = _resolve_pairing_id(event, ("tool_call_id", "call_id"))
        return f"tool:{pairing_id}" if pairing_id else _turn_span_key(event)
    if event_type.startswith("llm.call."):
        pairing_id = _resolve_pairing_id(
            event,
            ("llm_call_id", "call_id", "request_id"),
        )
        return f"llm:{pairing_id}" if pairing_id else _turn_span_key(event)
    if event_type.startswith("agent.handoff."):
        pairing_id = str(payload.get("handoff_id") or "")
        return f"handoff:{pairing_id}" if pairing_id else _turn_span_key(event)
    if event_type.startswith("agent.execution."):
        return _execution_span_key(event)
    return _turn_span_key(event) or _execution_span_key(event)


def _span_depth(span_key: str) -> int:
    return {"turn": 0, "phase": 1}.get(span_key.partition(":")[0], 2)


def event_export_dispositions() -> dict[str, str]:
    return dict(_EVENT_CLASSIFICATION)


def _trace_key_for_event(event: TelemetryEvent) -> str:
    trace_key = str(event.trace_key or "").strip()
    if trace_key:
        return trace_key
    payload = event.data
    for key in ("trace_id", "run_id", "request_id"):
        value = str(payload.get(key, "") or "").strip()
        if value:
            return value
    trace_value = str(event.turn_id or "").strip()
    if trace_value:
        return trace_value
    return str(event.session_id or "openminion-trace").strip() or "openminion-trace"


def _is_sampled(trace_key: str, sample_rate: float) -> bool:
    rate = max(0.0, min(1.0, float(sample_rate or 0.0)))
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    digest = hashlib.sha256(trace_key.encode("utf-8")).hexdigest()
    scaled = int(digest[:16], 16) / float(0xFFFFFFFFFFFFFFFF)
    return scaled < rate


def _is_terminal_event(event_type: str) -> bool:
    normalized = str(event_type or "").strip()
    if normalized in _TERMINAL_EVENT_TYPES:
        return True
    return normalized.startswith(_TERMINAL_EVENT_PREFIXES)


def _timestamp_ns(raw_timestamp: float) -> int:
    return max(1, int(float(raw_timestamp or 0.0) * 1_000_000_000))


def _resolve_pairing_id(
    event: TelemetryEvent,
    pairing_keys: tuple[str, ...],
) -> str:
    payload = event.data
    for key in pairing_keys:
        value = payload.get(key) or getattr(event, key, None)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


__all__ = [
    "_OpenTelemetrySDKSink",
    "ExportedOTELRecord",
    "OpenTelemetryTraceExporter",
    "OTELTraceSink",
    "RecordingOTELTraceSink",
    "create_otel_trace_sink",
    "event_export_dispositions",
]
