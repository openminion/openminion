"""Shared segment runtime primitives."""

from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
from typing import Any, Callable

from ..constants import (
    CONTEXT_DROP_VISIBILITY_BUCKET_LABELS,
    CONTEXT_DROP_VISIBILITY_NOTE_MAX_CHARS,
    PINNED_BUCKETS,
)
from ..schemas import ContextBudgets, ContextSegment
from .cache import segment_cache_fields


def _content_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def make_segment(
    seg_id: str,
    bucket: str,
    content: str,
    *,
    role: str = "system",
    refs: list[str] | None = None,
    is_artifact_preview: bool = False,
    pinned: bool = False,
    estimate_tokens: Callable[[str], int],
) -> ContextSegment:
    is_cacheable = bucket == "static_prefix"
    content_hash = _content_hash(content) if content else ""
    return ContextSegment(
        id=seg_id,
        bucket=bucket,  # type: ignore[arg-type]
        role=role,  # type: ignore[arg-type]
        content=content,
        token_estimate=estimate_tokens(content) if content else 0,
        content_hash=content_hash,
        refs=refs or [],
        is_artifact_preview=is_artifact_preview,
        is_cacheable=is_cacheable,
        **segment_cache_fields(bucket, content_hash),
        pinned=pinned or bucket in PINNED_BUCKETS,
    )


def _candidate_label(count: int) -> str:
    return "candidate" if count == 1 else "candidates"


def render_context_drop_visibility_note(drop_counts: dict[str, int]) -> str:
    items = [
        (CONTEXT_DROP_VISIBILITY_BUCKET_LABELS.get(bucket, bucket), count)
        for bucket, count in drop_counts.items()
        if count > 0
    ]
    if not items:
        return ""

    parts = [
        f"{count} {label} {_candidate_label(count)}" for label, count in sorted(items)
    ]
    if len(parts) == 1:
        counts_text = parts[0]
    else:
        counts_text = ", ".join(parts[:-1]) + f", and {parts[-1]}"
    verb = "was" if len(items) == 1 and items[0][1] == 1 else "were"
    note = f"[context budget: {counts_text} {verb} not included due to budget.]"
    return note[:CONTEXT_DROP_VISIBILITY_NOTE_MAX_CHARS].rstrip()


def inject_context_drop_visibility_note(
    *,
    segments: list[ContextSegment],
    drop_counts: dict[str, int],
    estimate_tokens: Callable[[str], int],
) -> list[ContextSegment]:
    note = render_context_drop_visibility_note(drop_counts)
    if not note:
        return segments

    note_segment = make_segment(
        "context_drop_visibility",
        "static_prefix",
        note,
        pinned=True,
        estimate_tokens=estimate_tokens,
    )
    insert_at = next(
        (
            idx + 1
            for idx, segment in enumerate(segments)
            if segment.bucket == "static_prefix"
        ),
        0,
    )
    return [*segments[:insert_at], note_segment, *segments[insert_at:]]


@dataclass
class _SegmentAssemblyRuntime:
    budgets: ContextBudgets
    fit_to_budget: Callable[[str, int], tuple[str, bool]]
    estimate_tokens: Callable[[str], int]
    segments: list[ContextSegment] = _dc_field(default_factory=list)
    bucket_stats: dict[str, Any] = _dc_field(default_factory=dict)
    truncation_stats: dict[str, int] = _dc_field(default_factory=dict)

    def fit_section(self, section: str, text: str, cap_tokens: int) -> str:
        fitted, truncated = self.fit_to_budget(text, cap_tokens)
        if truncated:
            self.truncation_stats[section] = self.truncation_stats.get(section, 0) + 1
        return fitted

    def make(
        self,
        seg_id: str,
        bucket: str,
        content: str,
        *,
        role: str = "system",
        refs: list[str] | None = None,
        is_artifact_preview: bool = False,
        pinned: bool = False,
    ) -> ContextSegment:
        return make_segment(
            seg_id,
            bucket,
            content,
            role=role,
            refs=refs,
            is_artifact_preview=is_artifact_preview,
            pinned=pinned,
            estimate_tokens=self.estimate_tokens,
        )
