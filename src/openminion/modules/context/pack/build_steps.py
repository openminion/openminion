from __future__ import annotations

from typing import Any, Callable

from ..memory_blocks import build_memory_block_segments_for_context
from ..schemas import BuildPackRequest, ContextBudgets, ContextSegment, PackingDecisionLog
from .budgeting import _estimate_tokens
from .finalize import context_drop_visibility_counts


def inject_memory_block_segments(
    *,
    enabled: bool,
    memory_block_store: Any,
    memory_client: Any,
    request: BuildPackRequest,
    budgets: ContextBudgets,
    segments: list[ContextSegment],
    bucket_stats: dict[str, Any],
) -> None:
    memory_segments, memory_stats = build_memory_block_segments_for_context(
        enabled=enabled,
        memory_block_store=memory_block_store,
        memory_client=memory_client,
        session_id=request.session_id,
        agent_id=request.agent_id,
        memory_token_budget=budgets.memory_tokens,
    )
    if memory_segments:
        turn_input_index = next(
            (
                index
                for index, segment in enumerate(segments)
                if segment.bucket == "turn_input"
            ),
            len(segments),
        )
        segments[turn_input_index:turn_input_index] = memory_segments
    if memory_stats:
        bucket_stats["memory"] = memory_stats


def trim_segments_for_pack(
    *,
    segments: list[ContextSegment],
    total_cap: int,
    bucket_caps: dict[str, int],
    bucket_stats: dict[str, Any],
    apply_trim_ladder_fn: Callable[..., tuple[list[ContextSegment], PackingDecisionLog, list[str]]],
    inject_visibility_note_fn: Callable[..., list[ContextSegment]],
) -> tuple[list[ContextSegment], PackingDecisionLog, list[str]]:
    decision_log = PackingDecisionLog()
    warnings: list[str] = []
    segments, decision_log, warnings = apply_trim_ladder_fn(
        segments=segments,
        total_cap=total_cap,
        bucket_caps=bucket_caps,
        decision_log=decision_log,
        warnings=warnings,
    )
    segments = inject_visibility_note_fn(
        segments=segments,
        drop_counts=context_drop_visibility_counts(
            decision_log=decision_log,
            bucket_stats=bucket_stats,
        ),
        estimate_tokens=_estimate_tokens,
    )
    return segments, decision_log, warnings
