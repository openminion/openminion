"""Outcome back-reference helpers for memory spans."""

from dataclasses import replace
from typing import Iterable, Sequence

from openminion.modules.memory.observability.span import MemorySpanRecord


def apply_outcome_tag(span: MemorySpanRecord, *, outcome_tag: str) -> MemorySpanRecord:
    return replace(span, outcome_tag=str(outcome_tag or "").strip() or None)


def backref_outcome_to_spans(
    spans: Sequence[MemorySpanRecord],
    *,
    outcome_tag: str,
    record_ids: Iterable[str] | None = None,
) -> list[MemorySpanRecord]:
    target = set(record_ids) if record_ids is not None else None
    return [
        apply_outcome_tag(span, outcome_tag=outcome_tag)
        if target is None or span.record_id in target
        else span
        for span in spans
    ]


__all__ = ["apply_outcome_tag", "backref_outcome_to_spans"]
