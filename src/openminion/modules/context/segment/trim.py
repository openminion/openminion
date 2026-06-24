"""Trim and layout helpers for context segment assembly."""

from typing import Callable

from ..constants import CONTEXT_BUCKET_RECENT_WINDOW, PINNED_BUCKETS, TRIM_ORDER
from ..schemas import ContextSegment, PackingDecisionLog, TrimAction


def position_aware_v1(
    segments: list[ContextSegment], scores: list[float]
) -> list[ContextSegment]:
    if not segments:
        return segments
    paired = sorted(zip(scores, segments), key=lambda x: x[0], reverse=True)
    result: list[ContextSegment | None] = [None] * len(paired)
    left, right = 0, len(paired) - 1
    for i, (_, seg) in enumerate(paired):
        if i % 2 == 0:
            result[left] = seg
            left += 1
        else:
            result[right] = seg
            right -= 1
    return [segment for segment in result if segment is not None]


class LayoutDisciplineError(RuntimeError):
    pass


def assert_layout_discipline(segments: list[ContextSegment]) -> None:
    seen_turn_input = False
    for segment in segments:
        if segment.bucket == "turn_input":
            seen_turn_input = True
        elif seen_turn_input and segment.content.strip():
            raise LayoutDisciplineError(
                f"Layout violation: segment '{segment.id}' (bucket={segment.bucket}) "
                "appears after turn_input"
            )


def apply_trim_ladder(
    segments: list[ContextSegment],
    total_cap: int,
    bucket_caps: dict[str, int],
    decision_log: PackingDecisionLog,
    warnings: list[str],
    *,
    estimate_tokens: Callable[[str], int],
) -> tuple[list[ContextSegment], PackingDecisionLog, list[str]]:
    def bucket_used_tokens(name: str) -> int:
        return sum(
            estimate_tokens(segment.content)
            for segment in segments
            if segment.bucket == name and segment.content.strip()
        )

    def drop_one_from_bucket(name: str, reason_code: str) -> int:
        candidates = [
            (idx, segment)
            for idx, segment in enumerate(segments)
            if segment.bucket == name and segment.content.strip() and not segment.pinned
        ]
        if not candidates:
            return 0
        idx, segment = (
            candidates[0] if name == CONTEXT_BUCKET_RECENT_WINDOW else candidates[-1]
        )
        saved = estimate_tokens(segment.content)
        segments[idx] = segment.model_copy(update={"content": ""})
        decision_log.append(
            TrimAction(
                action="drop_segment",
                reason_code=reason_code,
                segment_ids=[segment.id],
                bucket=name,
                tokens_saved=saved,
            )
        )
        warnings.append(f"drop_segment:{segment.id}")
        return saved

    total_tokens = sum(
        estimate_tokens(segment.content)
        for segment in segments
        if segment.content.strip()
    )

    for bucket_name, cap in bucket_caps.items():
        if bucket_name in PINNED_BUCKETS:
            continue
        while bucket_used_tokens(bucket_name) > cap:
            saved = drop_one_from_bucket(bucket_name, "bucket_cap")
            if saved <= 0:
                break
            total_tokens -= saved

    for bucket_name in TRIM_ORDER:
        if total_tokens <= total_cap:
            break
        if bucket_name in PINNED_BUCKETS:
            continue
        while total_tokens > total_cap:
            saved = drop_one_from_bucket(bucket_name, "over_budget")
            if saved <= 0:
                break
            total_tokens -= saved

    if total_tokens > total_cap:
        warnings.append(f"budget_exceeded:remaining={total_tokens - total_cap}tokens")

    return segments, decision_log, warnings
