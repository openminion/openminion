from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import time
from typing import Any, Protocol

from openminion.base.config import OTELExporterConfig
from ..schemas import TelemetryEvent
from .performance_metrics import performance_metrics_for_event

_LOG = logging.getLogger(__name__)
_PROSE_KEYS = frozenset(
    {
        "assistant_body",
        "assistant_text",
        "body",
        "content",
        "final_text",
        "message",
        "summary",
        "text",
        "user_message",
    }
)
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
_KIND_COUNTER = "counter"
_KIND_GAUGE = "gauge"
_KIND_HISTOGRAM = "histogram"

_EVENT_CLASSIFICATION: dict[str, str] = {
    "storage.query": _CLASS_SPAN,
    "storage.slow_query": _CLASS_SPAN,
    "storage.migration": _CLASS_SPAN,
    "storage.pool.stats": _CLASS_METRIC,
    "memory.scope_capacity.evicted": _CLASS_METRIC,
    "memory.soft_deleted.purged": _CLASS_METRIC,
    # LLM cache metrics — point-in-time hit/miss observation. Treat
    # as gauge per the metric-kind table; operator may flip to counter via
    # _METRIC_KIND_BY_EVENT if the source semantics shift.
    "llm.cache.metrics": _CLASS_METRIC,
    "chat.phase_timing": _CLASS_SPAN,
    "module.stats": _CLASS_METRIC,
    "tui.render": _CLASS_METRIC,
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
}
_PAIRED_COMPLETION_EVENTS: dict[str, tuple[str, tuple[str, ...]]] = {
    completion: (start, pairing_keys)
    for start, (completion, pairing_keys, _) in _PAIRED_SPAN_CLASSES.items()
}


class OTELTraceSink(Protocol):
    def emit_span(
        self,
        *,
        trace_key: str,
        session_id: str,
        turn_id: str,
        span_name: str,
        attributes: dict[str, Any],
        timestamp_ns: int,
        end_timestamp_ns: int | None = None,
    ) -> None: ...

    def emit_event(
        self,
        *,
        trace_key: str,
        session_id: str,
        turn_id: str,
        event_name: str,
        attributes: dict[str, Any],
        timestamp_ns: int,
        terminal: bool,
    ) -> None: ...

    def emit_metric(
        self,
        *,
        trace_key: str,
        session_id: str,
        turn_id: str,
        metric_name: str,
        metric_kind: str,
        value: float,
        attributes: dict[str, Any],
        timestamp_ns: int,
    ) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class ExportedOTELRecord:
    kind: str
    trace_key: str
    session_id: str
    turn_id: str
    name: str
    attributes: dict[str, Any]
    timestamp_ns: int
    terminal: bool = False
    # end timestamp for paired-span emission. None for non-paired
    # spans and for non-span records.
    end_timestamp_ns: int | None = None
    # OTel metric kind for metric records. Empty for non-metric
    # records. Valid values: ``"gauge"`` (point-in-time observation) or
    # ``"counter"`` (monotonic increment).
    metric_kind: str = ""
    # numeric value for metric records. Zero for non-metric records.
    metric_value: float = 0.0


class RecordingOTELTraceSink:
    """Test sink that records transformed spans/events without SDK deps."""

    def __init__(self) -> None:
        self.records: list[ExportedOTELRecord] = []

    def emit_span(
        self,
        *,
        trace_key: str,
        session_id: str,
        turn_id: str,
        span_name: str,
        attributes: dict[str, Any],
        timestamp_ns: int,
        end_timestamp_ns: int | None = None,
    ) -> None:
        self.records.append(
            ExportedOTELRecord(
                kind="span",
                trace_key=trace_key,
                session_id=session_id,
                turn_id=turn_id,
                name=span_name,
                attributes=dict(attributes),
                timestamp_ns=timestamp_ns,
                end_timestamp_ns=end_timestamp_ns,
            )
        )

    def emit_event(
        self,
        *,
        trace_key: str,
        session_id: str,
        turn_id: str,
        event_name: str,
        attributes: dict[str, Any],
        timestamp_ns: int,
        terminal: bool,
    ) -> None:
        self.records.append(
            ExportedOTELRecord(
                kind="event",
                trace_key=trace_key,
                session_id=session_id,
                turn_id=turn_id,
                name=event_name,
                attributes=dict(attributes),
                timestamp_ns=timestamp_ns,
                terminal=terminal,
            )
        )

    def emit_metric(
        self,
        *,
        trace_key: str,
        session_id: str,
        turn_id: str,
        metric_name: str,
        metric_kind: str,
        value: float,
        attributes: dict[str, Any],
        timestamp_ns: int,
    ) -> None:
        self.records.append(
            ExportedOTELRecord(
                kind="metric",
                trace_key=trace_key,
                session_id=session_id,
                turn_id=turn_id,
                name=metric_name,
                attributes=dict(attributes),
                timestamp_ns=timestamp_ns,
                metric_kind=metric_kind,
                metric_value=float(value),
            )
        )

    def close(self) -> None:
        return


class _OpenTelemetrySDKSink:
    def __init__(self, *, tracer: Any, provider: Any) -> None:
        self._tracer = tracer
        self._provider = provider
        self._root_spans: dict[str, Any] = {}
        self._metric_warned = False

    def emit_span(
        self,
        *,
        trace_key: str,
        session_id: str,
        turn_id: str,
        span_name: str,
        attributes: dict[str, Any],
        timestamp_ns: int,
        end_timestamp_ns: int | None = None,
    ) -> None:
        from opentelemetry.trace import set_span_in_context

        parent = self._ensure_root_span(
            trace_key=trace_key,
            session_id=session_id,
            turn_id=turn_id,
            timestamp_ns=timestamp_ns,
        )
        child = self._tracer.start_span(
            span_name,
            context=set_span_in_context(parent),
            start_time=timestamp_ns,
        )
        for key, value in attributes.items():
            child.set_attribute(key, value)
        child.end(
            end_time=end_timestamp_ns if end_timestamp_ns is not None else timestamp_ns
        )

    def emit_metric(
        self,
        *,
        trace_key: str,
        session_id: str,
        turn_id: str,
        metric_name: str,
        metric_kind: str,
        value: float,
        attributes: dict[str, Any],
        timestamp_ns: int,
    ) -> None:
        if not self._metric_warned:
            _LOG.info(
                "Metric emission (%s=%s) reached the SDK sink before the OTel "
                "metrics provider is wired. Recording sink coverage is in place.",
                metric_name,
                value,
            )
            self._metric_warned = True

    def emit_event(
        self,
        *,
        trace_key: str,
        session_id: str,
        turn_id: str,
        event_name: str,
        attributes: dict[str, Any],
        timestamp_ns: int,
        terminal: bool,
    ) -> None:
        root = self._ensure_root_span(
            trace_key=trace_key,
            session_id=session_id,
            turn_id=turn_id,
            timestamp_ns=timestamp_ns,
        )
        root.add_event(event_name, attributes=attributes, timestamp=timestamp_ns)
        if terminal:
            root.end(end_time=timestamp_ns)
            self._root_spans.pop(trace_key, None)

    def close(self) -> None:
        for trace_key, span in list(self._root_spans.items()):
            try:
                span.end(end_time=time.time_ns())
            except Exception:  # noqa: BLE001
                pass
            self._root_spans.pop(trace_key, None)
        self._provider.force_flush()
        self._provider.shutdown()

    def _ensure_root_span(
        self,
        *,
        trace_key: str,
        session_id: str,
        turn_id: str,
        timestamp_ns: int,
    ) -> Any:
        root = self._root_spans.get(trace_key)
        if root is not None:
            return root
        root = self._tracer.start_span(
            "openminion.turn",
            start_time=timestamp_ns,
            attributes={
                "openminion.trace_key": trace_key,
                "openminion.session_id": session_id,
                "openminion.turn_id": turn_id,
            },
        )
        self._root_spans[trace_key] = root
        return root


def create_otel_trace_sink(
    config: OTELExporterConfig,
    *,
    logger: logging.Logger | None = None,
) -> OTELTraceSink | None:
    if not bool(config.enabled):
        return None
    endpoint = str(config.endpoint or "").strip()
    if not endpoint:
        return None
    log = logger or _LOG
    try:
        if str(config.protocol or "").strip().lower() == "grpc":
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception as exc:  # noqa: BLE001
        log.warning("OpenTelemetry SDK unavailable; OTLP export disabled: %s", exc)
        return None

    resource = Resource.create(
        {"service.name": str(config.service_name or "openminion")}
    )
    provider = TracerProvider(resource=resource)
    headers = dict(getattr(config, "headers", {}) or {})
    exporter_kwargs: dict[str, Any] = {"endpoint": endpoint}
    if headers:
        exporter_kwargs["headers"] = headers
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(**exporter_kwargs)))
    tracer = provider.get_tracer("openminion.telemetry.otel")
    return _OpenTelemetrySDKSink(tracer=tracer, provider=provider)


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
        # explicit exclusion check before any work — METRIC/MESSAGE
        # generic catchalls (OTEL-02 §4.3 item 12) do not reach the sink.
        if _EVENT_CLASSIFICATION.get(event_type) == _CLASS_EXCLUDED:
            return False
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
                return True
            if event_type in _PAIRED_COMPLETION_EVENTS:
                if self._emit_paired_completion(
                    event_type=event_type,
                    event=event,
                    attributes=attributes,
                    timestamp_ns=timestamp_ns,
                    trace_key=trace_key,
                ):
                    return True
            classification = _EVENT_CLASSIFICATION.get(event_type)
            if classification is None and event_type.startswith("tool."):
                classification = _CLASS_SPAN
            if classification == _CLASS_SPAN:
                self._sink.emit_span(
                    trace_key=trace_key,
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                    span_name=event_type or "openminion.event",
                    attributes=attributes,
                    timestamp_ns=timestamp_ns,
                )
                self._emit_performance_metrics(
                    event=event,
                    timestamp_ns=timestamp_ns,
                    trace_key=trace_key,
                )
            elif classification == _CLASS_METRIC:
                self._sink.emit_metric(
                    trace_key=trace_key,
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                    metric_name=event_type or "openminion.event",
                    metric_kind=_metric_kind_for_event(event_type),
                    value=_metric_value_for_event(event),
                    attributes=attributes,
                    timestamp_ns=timestamp_ns,
                )
                self._emit_performance_metrics(
                    event=event,
                    timestamp_ns=timestamp_ns,
                    trace_key=trace_key,
                )
            else:
                self._sink.emit_event(
                    trace_key=trace_key,
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                    event_name=event_type or "event",
                    attributes=attributes,
                    timestamp_ns=timestamp_ns,
                    terminal=_is_terminal_event(event.event_type),
                )
            return True
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "OpenTelemetry export failed for event_type=%s: %s",
                event.event_type,
                exc,
            )
            return False

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
            # No pairing key — fall back to a log record at the sink so the
            # signal is not silently lost.
            self._sink.emit_event(  # type: ignore[union-attr]
                trace_key=trace_key,
                session_id=event.session_id,
                turn_id=event.turn_id,
                event_name=event_type,
                attributes=attributes,
                timestamp_ns=timestamp_ns,
                terminal=False,
            )
            return
        slot = f"{event_type}:{pairing_id}"
        if (
            slot not in self._pending_paired_spans
            and len(self._pending_paired_spans) >= self._MAX_PENDING_PAIRED_SPANS
        ):
            # Evict the oldest pending start to bound memory.
            oldest = next(iter(self._pending_paired_spans))
            self._pending_paired_spans.pop(oldest, None)
        self._pending_paired_spans[slot] = {
            "trace_key": trace_key,
            "session_id": event.session_id,
            "turn_id": event.turn_id,
            "attributes": dict(attributes),
            "start_timestamp_ns": timestamp_ns,
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
        span_name = _PAIRED_SPAN_CLASSES[start_event_type][2]
        self._sink.emit_span(  # type: ignore[union-attr]
            trace_key=trace_key,
            session_id=event.session_id,
            turn_id=event.turn_id,
            span_name=span_name,
            attributes=merged_attributes,
            timestamp_ns=int(pending["start_timestamp_ns"]),
            end_timestamp_ns=timestamp_ns,
        )
        return True

    def close(self) -> None:
        if self._sink is None:
            return
        try:
            self._sink.close()
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("OpenTelemetry exporter shutdown failed: %s", exc)
        finally:
            self._sink = None
            self._pending_paired_spans.clear()


def _attributes_for_event(
    event: TelemetryEvent,
    *,
    include_assistant_body: bool,
) -> dict[str, Any]:
    flattened: dict[str, Any] = {
        "openminion.event_type": str(event.event_type or ""),
        "openminion.session_id": str(event.session_id or ""),
        "openminion.turn_id": str(event.turn_id or ""),
    }
    if event.mode:
        flattened["openminion.mode"] = str(event.mode)
    _flatten_payload(
        event.data,
        prefix="openminion.payload",
        out=flattened,
        include_assistant_body=include_assistant_body,
    )
    flattened.update(_gen_ai_attributes_for_event(event))
    return flattened


_GEN_AI_LLM_EVENT_TYPES = frozenset(
    {
        "llm.call.started",
        "llm.call.completed",
        "llm.request.started",
        "llm.ensemble.completed",
        "llm.candidate.finished",
        "llm.judge.completed",
        "llm_call",
    }
)
_GEN_AI_INPUT_TOKEN_KEYS = ("input_tokens", "prompt_tokens")
_GEN_AI_OUTPUT_TOKEN_KEYS = ("output_tokens", "completion_tokens")


def _gen_ai_attributes_for_event(event: TelemetryEvent) -> dict[str, Any]:
    """Return OTel GenAI semantic attributes for LLM telemetry events."""

    event_type = str(event.event_type or "").strip()
    if event_type not in _GEN_AI_LLM_EVENT_TYPES:
        return {}

    payload = event.data if isinstance(event.data, dict) else {}
    attributes: dict[str, Any] = {
        "gen_ai.operation.name": "chat",
    }

    model = payload.get("model") or payload.get("model_id")
    if model:
        attributes["gen_ai.request.model"] = str(model)

    provider = (
        payload.get("provider") or payload.get("provider_name") or payload.get("vendor")
    )
    if provider:
        attributes["gen_ai.system"] = str(provider)
    elif model:
        attributes["gen_ai.system"] = _infer_gen_ai_system_from_model(str(model))

    usage = payload.get("usage")
    if isinstance(usage, dict):
        input_tokens = _first_int(usage, _GEN_AI_INPUT_TOKEN_KEYS)
        if input_tokens is not None:
            attributes["gen_ai.usage.input_tokens"] = input_tokens
        output_tokens = _first_int(usage, _GEN_AI_OUTPUT_TOKEN_KEYS)
        if output_tokens is not None:
            attributes["gen_ai.usage.output_tokens"] = output_tokens

    response_id = (
        payload.get("response_id")
        or payload.get("llm_call_id")
        or payload.get("request_id")
    )
    if response_id:
        attributes["gen_ai.response.id"] = str(response_id)

    finish_reason = payload.get("finish_reason") or payload.get("stop_reason")
    if finish_reason:
        attributes["gen_ai.response.finish_reasons"] = json.dumps(
            [str(finish_reason)],
            ensure_ascii=True,
            separators=(",", ":"),
        )

    return attributes


def _infer_gen_ai_system_from_model(model: str) -> str:
    """Best-effort provider name inference from a model id.

    Used when telemetry payloads carry the model but not an explicit provider.
    Returns ``"openminion"`` as the fallback so the attribute is always set on
    LLM events (per the OTel spec recommendation).
    """

    lower = model.lower()
    if lower.startswith("claude") or lower.startswith("anthropic"):
        return "anthropic"
    if lower.startswith("gpt") or lower.startswith("o1") or "openai" in lower:
        return "openai"
    if lower.startswith("gemini") or "google" in lower:
        return "google"
    if "llama" in lower:
        return "meta"
    if "mistral" in lower:
        return "mistral"
    if "groq" in lower:
        return "groq"
    if "cerebras" in lower:
        return "cerebras"
    if "ollama" in lower:
        return "ollama"
    return "openminion"


def _first_int(source: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _flatten_payload(
    value: Any,
    *,
    prefix: str,
    out: dict[str, Any],
    include_assistant_body: bool,
) -> None:
    key_name = prefix.rsplit(".", 1)[-1].lower()
    if isinstance(value, dict):
        for key, item in value.items():
            clean_key = str(key or "").strip()
            if not clean_key:
                continue
            _flatten_payload(
                item,
                prefix=f"{prefix}.{clean_key}",
                out=out,
                include_assistant_body=include_assistant_body,
            )
        return
    if isinstance(value, (list, tuple)):
        if not include_assistant_body and key_name in _PROSE_KEYS:
            return
        out[prefix] = json.dumps(
            _normalize_otel_json_value(
                list(value),
                include_assistant_body=include_assistant_body,
            ),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return
    if isinstance(value, bool | int | float):
        out[prefix] = value
        return
    if value is None:
        return
    text = str(value)
    if not include_assistant_body and key_name in _PROSE_KEYS:
        return
    out[prefix] = text


def _normalize_otel_json_value(
    value: Any,
    *,
    include_assistant_body: bool,
) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            clean_key = str(key or "").strip()
            if not clean_key:
                continue
            if not include_assistant_body and clean_key.lower() in _PROSE_KEYS:
                continue
            normalized[clean_key] = _normalize_otel_json_value(
                item,
                include_assistant_body=include_assistant_body,
            )
        return normalized
    if isinstance(value, (list, tuple)):
        return [
            _normalize_otel_json_value(
                item,
                include_assistant_body=include_assistant_body,
            )
            for item in value
        ]
    if isinstance(value, bool | int | float) or value is None:
        return value
    return str(value)


def _trace_key_for_event(event: TelemetryEvent) -> str:
    payload = event.data if isinstance(event.data, dict) else {}
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


_METRIC_KIND_BY_EVENT: dict[str, str] = {
    "storage.pool.stats": _KIND_GAUGE,
    "memory.scope_capacity.evicted": _KIND_COUNTER,
    "memory.soft_deleted.purged": _KIND_COUNTER,
    # OTEL-04 additions
    "llm.cache.metrics": _KIND_GAUGE,
    "module.stats": _KIND_GAUGE,
    "tui.render": _KIND_HISTOGRAM,
}


def _metric_kind_for_event(event_type: str) -> str:
    return _METRIC_KIND_BY_EVENT.get(event_type, "gauge")


_METRIC_VALUE_KEYS: tuple[str, ...] = (
    "value",
    "count",
    "delta",
    "total",
    "size",
    "depth",
    "active",
    "pool_size",
)


def _metric_value_for_event(event: TelemetryEvent) -> float:
    payload = event.data if isinstance(event.data, dict) else {}
    for key in _METRIC_VALUE_KEYS:
        candidate = payload.get(key)
        if candidate is None:
            continue
        try:
            return float(candidate)
        except (TypeError, ValueError):
            continue
    return 1.0


def _resolve_pairing_id(
    event: TelemetryEvent,
    pairing_keys: tuple[str, ...],
) -> str:
    payload = event.data if isinstance(event.data, dict) else {}
    for key in pairing_keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


__all__ = [
    "ExportedOTELRecord",
    "OpenTelemetryTraceExporter",
    "OTELTraceSink",
    "RecordingOTELTraceSink",
    "create_otel_trace_sink",
]
