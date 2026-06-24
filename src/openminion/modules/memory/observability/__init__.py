"""Public exports for memory-span observability."""

from .emitter import SpanEmitter, SpanReadContext, record_span_read
from .outcome import apply_outcome_tag, backref_outcome_to_spans
from .span import (
    MemorySpanRecord,
    StaleReadSignal,
    build_span_record,
    detect_stale_read,
    span_telemetry_payload,
)

__all__ = [
    "MemorySpanRecord",
    "SpanEmitter",
    "SpanReadContext",
    "StaleReadSignal",
    "apply_outcome_tag",
    "backref_outcome_to_spans",
    "build_span_record",
    "detect_stale_read",
    "record_span_read",
    "span_telemetry_payload",
]
