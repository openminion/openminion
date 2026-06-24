"""Span emitter for memory-read observability."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from openminion.modules.memory.observability.span import (
    MemorySpanRecord,
    build_span_record,
    span_telemetry_payload,
)
from openminion.modules.telemetry.events.catalog import MSPO_MEMORY_SPAN_READ


@dataclass(frozen=True)
class SpanReadContext:
    record_id: str
    relevance_score: float
    freshness_at_read: float
    scope: str = ""
    session_id: str = ""


@dataclass
class SpanEmitter:
    logger: Any = None
    spans: list[MemorySpanRecord] = field(default_factory=list)

    def record(self, ctx: SpanReadContext) -> MemorySpanRecord:
        span = build_span_record(
            span_id=f"span-{uuid.uuid4().hex[:12]}",
            record_id=ctx.record_id,
            relevance_score=ctx.relevance_score,
            freshness_at_read=ctx.freshness_at_read,
            read_at=datetime.now(timezone.utc).isoformat(),
            scope=ctx.scope,
            session_id=ctx.session_id,
        )
        self.spans.append(span)
        _stamp_telemetry(self.logger, span)
        return span

    def by_record(self, record_id: str) -> list[MemorySpanRecord]:
        return [s for s in self.spans if s.record_id == record_id]

    def by_session(self, session_id: str) -> list[MemorySpanRecord]:
        return [s for s in self.spans if s.session_id == session_id]


def record_span_read(emitter: SpanEmitter, ctx: SpanReadContext) -> MemorySpanRecord:
    return emitter.record(ctx)


def _stamp_telemetry(logger: Any, span: MemorySpanRecord) -> None:
    if logger is None:
        return
    try:
        logger.log_canonical_event(
            event_type=MSPO_MEMORY_SPAN_READ,
            payload=span_telemetry_payload(span),
        )
    except Exception:
        pass


def iter_spans(spans: Iterable[MemorySpanRecord]) -> Iterable[MemorySpanRecord]:
    yield from spans


__all__ = [
    "SpanEmitter",
    "SpanReadContext",
    "iter_spans",
    "record_span_read",
]
