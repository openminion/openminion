"""Recent-turn role mapping and render-message conversion."""

from __future__ import annotations

from typing import Any, Callable

from ..constants import CONTEXT_BUCKET_RECENT_WINDOW, CONTEXT_PURPOSE_DECIDE, RECENT_TURN_ASSISTANT_MAX_TOKENS
from ..schemas import ContextSegment, RenderMessage, SessionTurn
from .cache import segment_render_cache_metadata


def map_turn_role(role: str) -> str:
    normalized = role.strip().lower()
    if normalized in {"user", "assistant", "system", "tool"}:
        return normalized
    return (
        "user"
        if normalized in {"inbound"}
        else "assistant"
        if normalized == "outbound"
        else "user"
    )


def protected_decide_recent_turn_indexes(
    recent_turns: list[SessionTurn], *, purpose: str
) -> set[int]:
    if purpose == CONTEXT_PURPOSE_DECIDE:
        for idx in range(len(recent_turns) - 1, -1, -1):
            if map_turn_role(recent_turns[idx].role) != "assistant":
                continue
            protected = {idx}
            if idx > 0 and map_turn_role(recent_turns[idx - 1].role) == "user":
                protected.add(idx - 1)
            return protected
        return set()

    has_assistant = any(
        map_turn_role(turn.role) == "assistant" for turn in recent_turns
    )
    if has_assistant:
        return set()
    user_indexes = [
        idx
        for idx, turn in enumerate(recent_turns)
        if map_turn_role(turn.role) == "user"
    ]
    if not user_indexes:
        return set()
    protected: set[int] = {user_indexes[0]}
    if len(user_indexes) > 1:
        protected.add(user_indexes[-1])
    return protected


def assistant_tail_for_recent_window(
    turn: SessionTurn,
    *,
    purpose: str,
    estimate_tokens: Callable[[str], int],
    preserve_full: bool = False,
) -> str:
    content = str(turn.content or "")
    if (
        preserve_full
        or purpose != CONTEXT_PURPOSE_DECIDE
        or map_turn_role(turn.role) != "assistant"
    ):
        return content
    if estimate_tokens(content) <= RECENT_TURN_ASSISTANT_MAX_TOKENS:
        return content
    marker = "[...truncated, full response available in session]\n"
    max_chars = RECENT_TURN_ASSISTANT_MAX_TOKENS * 4
    return marker + content[-max_chars:].lstrip()


def _bucket_order_index(bucket: str, bucket_order: list[str]) -> int:
    return bucket_order.index(bucket) if bucket in bucket_order else 99


def _merge_system_segments(
    ordered: list[ContextSegment],
) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]], dict[str, list[str]], dict[str, list[str]]]:
    merged_system: dict[str, list[str]] = {}
    merged_system_cache_control: dict[str, dict[str, Any]] = {}
    merged_system_segment_ids: dict[str, list[str]] = {}
    merged_system_refs: dict[str, list[str]] = {}
    for segment in ordered:
        if not segment.content.strip():
            continue
        if segment.role in {"user", "assistant", "tool"}:
            continue
        if segment.bucket == CONTEXT_BUCKET_RECENT_WINDOW:
            continue
        merged_system.setdefault(segment.bucket, []).append(segment.content)
        merged_system_segment_ids.setdefault(segment.bucket, []).append(segment.id)
        refs = merged_system_refs.setdefault(segment.bucket, [])
        for ref in segment.refs:
            if ref not in refs:
                refs.append(ref)
        if segment.is_cacheable:
            merged_system_cache_control.setdefault(segment.bucket, {"type": "ephemeral"})
    return (
        merged_system,
        merged_system_cache_control,
        merged_system_segment_ids,
        merged_system_refs,
    )


def segments_to_messages(segments: list[ContextSegment]) -> list[RenderMessage]:
    bucket_order = [
        "static_prefix",
        "mission_snapshot",
        "budget_telemetry",
        "task_digest",
        "summaries",
        "conversation_summary",
        "active_plan",
        "trailer_feedback",
        CONTEXT_BUCKET_RECENT_WINDOW,
        "retrieval",
        "evidence_refs",
        "turn_input",
    ]
    ordered = sorted(segments, key=lambda segment: _bucket_order_index(segment.bucket, bucket_order))
    (
        merged_system,
        merged_system_cache_control,
        merged_system_segment_ids,
        merged_system_refs,
    ) = _merge_system_segments(ordered)

    result: list[RenderMessage] = []
    seen_buckets: set[str] = set()
    for segment in ordered:
        if not segment.content.strip():
            continue
        if segment.bucket in merged_system and segment.bucket not in seen_buckets:
            seen_buckets.add(segment.bucket)
            result.append(
                RenderMessage(
                    role="system",
                    content="\n\n".join(merged_system[segment.bucket]),
                    cache_control=merged_system_cache_control.get(segment.bucket),
                    meta={
                        "block_kind": segment.bucket,
                        "cache_eligible": bool(segment.bucket in merged_system_cache_control),
                        "segment_ids": list(merged_system_segment_ids.get(segment.bucket, [])),
                        "refs": list(merged_system_refs.get(segment.bucket, [])),
                        **segment_render_cache_metadata(segment),
                    },
                )
            )
        elif segment.bucket == CONTEXT_BUCKET_RECENT_WINDOW or segment.role in {"user", "assistant", "tool"}:
            result.append(
                RenderMessage(
                    role=segment.role,  # type: ignore[arg-type]
                    content=segment.content,
                    meta={
                        "block_kind": segment.bucket,
                        "cache_eligible": bool(segment.is_cacheable),
                        "segment_ids": [segment.id],
                        "refs": list(segment.refs),
                        **segment_render_cache_metadata(segment),
                    },
                )
            )
    return result
