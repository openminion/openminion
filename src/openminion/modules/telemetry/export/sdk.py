from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from openminion.base.config import OTELExporterConfig


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
        span_kind: str = "INTERNAL",
        parent_traceparent: str = "",
        tracestate: str = "",
        span_links: tuple[str, ...] = (),
        span_key: str = "",
        parent_span_key: str = "",
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
        parent_span_key: str = "",
    ) -> None: ...

    def emit_metric(
        self,
        *,
        trace_key: str,
        session_id: str,
        turn_id: str,
        metric_name: str,
        metric_kind: str,
        unit: str,
        value: float,
        attributes: dict[str, Any],
        timestamp_ns: int,
    ) -> None: ...

    def emit_log(
        self,
        *,
        trace_key: str,
        session_id: str,
        turn_id: str,
        record_type: str,
        event_name: str,
        severity: str,
        body: str,
        attributes: dict[str, Any],
        timestamp_ns: int,
        parent_span_key: str = "",
    ) -> None: ...

    def close(self) -> None: ...

    def force_flush(self, timeout_seconds: float) -> bool: ...

    def release_trace(self, trace_key: str) -> None: ...


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
    end_timestamp_ns: int | None = None
    metric_kind: str = ""
    metric_value: float = 0.0
    metric_unit: str = ""
    span_kind: str = ""
    parent_traceparent: str = ""
    tracestate: str = ""
    span_links: tuple[str, ...] = ()
    span_key: str = ""
    parent_span_key: str = ""


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
        span_kind: str = "INTERNAL",
        parent_traceparent: str = "",
        tracestate: str = "",
        span_links: tuple[str, ...] = (),
        span_key: str = "",
        parent_span_key: str = "",
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
                span_kind=span_kind,
                parent_traceparent=parent_traceparent,
                tracestate=tracestate,
                span_links=span_links,
                span_key=span_key,
                parent_span_key=parent_span_key,
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
        parent_span_key: str = "",
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
                parent_span_key=parent_span_key,
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
        unit: str,
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
                metric_unit=unit,
            )
        )

    def emit_log(
        self,
        *,
        trace_key: str,
        session_id: str,
        turn_id: str,
        record_type: str,
        event_name: str,
        severity: str,
        body: str,
        attributes: dict[str, Any],
        timestamp_ns: int,
        parent_span_key: str = "",
    ) -> None:
        self.records.append(
            ExportedOTELRecord(
                kind="event_record" if record_type == "EventRecord" else "log_record",
                trace_key=trace_key,
                session_id=session_id,
                turn_id=turn_id,
                name=event_name,
                attributes={
                    "openminion.log.record_type": record_type,
                    "openminion.log.severity": severity,
                    "openminion.log.body": body,
                    **attributes,
                },
                timestamp_ns=timestamp_ns,
                parent_span_key=parent_span_key,
            )
        )

    def close(self) -> None:
        return

    def force_flush(self, timeout_seconds: float) -> bool:
        del timeout_seconds
        return True

    def release_trace(self, trace_key: str) -> None:
        del trace_key


class OpenTelemetrySDKSink:
    def __init__(
        self,
        *,
        tracer: Any,
        trace_provider: Any,
        meter: Any | None = None,
        metric_provider: Any | None = None,
        logger: Any | None = None,
        logger_provider: Any | None = None,
    ) -> None:
        self._tracer = tracer
        self._trace_provider = trace_provider
        self._meter = meter
        self._metric_provider = metric_provider
        self._logger = logger
        self._logger_provider = logger_provider
        self._metric_instruments: dict[tuple[str, str, str], Any] = {}
        self._gauge_values: dict[tuple[str, tuple[tuple[str, Any], ...]], float] = {}
        self._span_contexts: dict[tuple[str, str], Any] = {}

    def _local_parent_context(self, trace_key: str, parent_span_key: str) -> Any | None:
        if not parent_span_key:
            return None
        span_context = self._span_contexts.get((trace_key, parent_span_key))
        if span_context is None:
            return None
        from opentelemetry.trace import NonRecordingSpan, set_span_in_context

        return set_span_in_context(NonRecordingSpan(span_context))

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
        span_kind: str = "INTERNAL",
        parent_traceparent: str = "",
        tracestate: str = "",
        span_links: tuple[str, ...] = (),
        span_key: str = "",
        parent_span_key: str = "",
    ) -> None:
        from opentelemetry.trace import SpanKind

        parent_context = _context_from_traceparent(parent_traceparent, tracestate)
        if parent_context is None:
            parent_context = self._local_parent_context(trace_key, parent_span_key)
        child = self._tracer.start_span(
            span_name,
            context=parent_context,
            start_time=timestamp_ns,
            attributes={
                "openminion.trace_key": trace_key,
                "openminion.session_id": session_id,
                "openminion.turn_id": turn_id,
                **attributes,
            },
            kind=getattr(SpanKind, span_kind),
            links=_links(span_links),
        )
        if attributes.get("error.type"):
            from opentelemetry.trace import Status, StatusCode

            child.set_status(Status(StatusCode.ERROR))
        if span_key:
            self._span_contexts[(trace_key, span_key)] = child.get_span_context()
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
        unit: str,
        value: float,
        attributes: dict[str, Any],
        timestamp_ns: int,
    ) -> None:
        if self._meter is None:
            return
        instrument = self._metric_instrument(metric_name, metric_kind, unit)
        clean_attributes = dict(attributes)
        if metric_kind == "histogram":
            instrument.record(float(value), clean_attributes)
            return
        if metric_kind == "counter":
            instrument.add(max(0.0, float(value)), clean_attributes)
            return
        if hasattr(instrument, "set"):
            instrument.set(float(value), clean_attributes)
            return
        gauge_key = (
            metric_name,
            tuple(sorted((str(key), value) for key, value in clean_attributes.items())),
        )
        previous = self._gauge_values.get(gauge_key, 0.0)
        current = float(value)
        instrument.add(current - previous, clean_attributes)
        self._gauge_values[gauge_key] = current

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
        parent_span_key: str = "",
    ) -> None:
        span = self._tracer.start_span(
            "openminion.event",
            context=self._local_parent_context(trace_key, parent_span_key),
            start_time=timestamp_ns,
            attributes={
                "openminion.trace_key": trace_key,
                "openminion.session_id": session_id,
                "openminion.turn_id": turn_id,
            },
        )
        span.add_event(event_name, attributes=attributes, timestamp=timestamp_ns)
        span.end(end_time=timestamp_ns)

    def emit_log(
        self,
        *,
        trace_key: str,
        session_id: str,
        turn_id: str,
        record_type: str,
        event_name: str,
        severity: str,
        body: str,
        attributes: dict[str, Any],
        timestamp_ns: int,
        parent_span_key: str = "",
    ) -> None:
        if self._logger is None:
            return
        from opentelemetry._logs import SeverityNumber

        self._logger.emit(
            timestamp=timestamp_ns,
            observed_timestamp=timestamp_ns,
            context=self._local_parent_context(trace_key, parent_span_key),
            severity_text=severity,
            severity_number=getattr(SeverityNumber, severity),
            body=body,
            event_name=event_name,
            attributes={
                "openminion.trace_key": trace_key,
                "openminion.session_id": session_id,
                "openminion.turn_id": turn_id,
                "openminion.log.record_type": record_type,
                "event.name": event_name,
                **attributes,
            },
        )

    def close(self) -> None:
        self._trace_provider.force_flush()
        self._trace_provider.shutdown()
        if self._metric_provider is not None:
            self._metric_provider.force_flush()
            self._metric_provider.shutdown()
        if self._logger_provider is not None:
            self._logger_provider.force_flush()
            self._logger_provider.shutdown()
        self._span_contexts.clear()

    def force_flush(self, timeout_seconds: float) -> bool:
        timeout_millis = max(1, int(timeout_seconds * 1000))
        providers = (
            self._trace_provider,
            self._metric_provider,
            self._logger_provider,
        )
        for provider in providers:
            if provider is not None and provider.force_flush(timeout_millis) is False:
                return False
        return True

    def release_trace(self, trace_key: str) -> None:
        stale = [key for key in self._span_contexts if key[0] == trace_key]
        for key in stale:
            self._span_contexts.pop(key, None)

    def _metric_instrument(
        self,
        metric_name: str,
        metric_kind: str,
        unit: str,
    ) -> Any:
        key = (metric_name, metric_kind, unit)
        instrument = self._metric_instruments.get(key)
        if instrument is not None:
            return instrument
        assert self._meter is not None
        if metric_kind == "histogram":
            instrument = self._meter.create_histogram(metric_name, unit=unit)
        elif metric_kind == "counter":
            instrument = self._meter.create_counter(metric_name, unit=unit)
        elif hasattr(self._meter, "create_gauge"):
            instrument = self._meter.create_gauge(metric_name, unit=unit)
        else:
            instrument = self._meter.create_up_down_counter(metric_name, unit=unit)
        self._metric_instruments[key] = instrument
        return instrument


def create_otel_trace_sink(
    config: OTELExporterConfig,
    *,
    logger: logging.Logger,
) -> OTELTraceSink | None:
    endpoint = str(config.endpoint or "").strip()
    if not config.enabled or not endpoint:
        return None
    protocol = str(config.protocol or "").strip().lower()
    if protocol not in {"http", "http/protobuf", "grpc"}:
        logger.warning(
            "Unsupported OpenTelemetry protocol %r; OTLP export disabled",
            config.protocol,
        )
        return None
    span_exporter_class: Any
    metric_exporter_class: Any
    log_exporter_class: Any
    try:
        if protocol == "grpc":
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter as GrpcSpanExporter,
            )
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                OTLPMetricExporter as GrpcMetricExporter,
            )
            from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
                OTLPLogExporter as GrpcLogExporter,
            )

            span_exporter_class = GrpcSpanExporter
            metric_exporter_class = GrpcMetricExporter
            log_exporter_class = GrpcLogExporter
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter as HttpSpanExporter,
            )
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter as HttpMetricExporter,
            )
            from opentelemetry.exporter.otlp.proto.http._log_exporter import (
                OTLPLogExporter as HttpLogExporter,
            )

            span_exporter_class = HttpSpanExporter
            metric_exporter_class = HttpMetricExporter
            log_exporter_class = HttpLogExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    except ImportError as exc:
        logger.warning("OpenTelemetry SDK unavailable; OTLP export disabled: %s", exc)
        return None

    resource = Resource.create(
        {"service.name": str(config.service_name or "openminion")}
    )
    trace_provider = TracerProvider(resource=resource)
    exporter_kwargs: dict[str, Any] = {}
    if config.headers:
        exporter_kwargs["headers"] = dict(config.headers)
    signal_endpoints = (
        {signal: endpoint for signal in ("traces", "metrics", "logs")}
        if protocol == "grpc"
        else {
            signal: _http_signal_endpoint(endpoint, signal)
            for signal in ("traces", "metrics", "logs")
        }
    )
    trace_provider.add_span_processor(
        BatchSpanProcessor(
            span_exporter_class(endpoint=signal_endpoints["traces"], **exporter_kwargs)
        )
    )
    metric_reader = PeriodicExportingMetricReader(
        metric_exporter_class(
            endpoint=signal_endpoints["metrics"], **exporter_kwargs
        )
    )
    metric_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            log_exporter_class(endpoint=signal_endpoints["logs"], **exporter_kwargs)
        )
    )
    return OpenTelemetrySDKSink(
        tracer=trace_provider.get_tracer("openminion.telemetry.otel"),
        trace_provider=trace_provider,
        meter=metric_provider.get_meter("openminion.telemetry.performance"),
        metric_provider=metric_provider,
        logger=logger_provider.get_logger("openminion.telemetry.logs"),
        logger_provider=logger_provider,
    )


def _context_from_traceparent(traceparent: str, tracestate: str) -> Any | None:
    if not traceparent:
        return None
    from opentelemetry.propagate import extract

    carrier = {"traceparent": traceparent}
    if tracestate:
        carrier["tracestate"] = tracestate
    return extract(carrier)


def _http_signal_endpoint(endpoint: str, signal: str) -> str:
    parsed = urlsplit(endpoint)
    path = parsed.path.rstrip("/")
    for known_signal in ("traces", "metrics", "logs"):
        suffix = f"/v1/{known_signal}"
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit(parsed._replace(path=f"{path}/v1/{signal}"))


def _links(traceparents: tuple[str, ...]) -> list[Any]:
    if not traceparents:
        return []
    from opentelemetry.trace import Link, get_current_span

    links: list[Any] = []
    for traceparent in traceparents:
        context = _context_from_traceparent(traceparent, "")
        if context is None:
            continue
        span_context = get_current_span(context).get_span_context()
        if span_context.is_valid:
            links.append(Link(span_context))
    return links
