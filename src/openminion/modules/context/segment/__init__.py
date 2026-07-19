"""Public segment-assembly facade plus orchestration entry point."""

from typing import Any, Callable

from ..render.sections import _render_trailer_feedback
from ..retrieval import (
    collect_retrieval_bundle as _collect_retrieval_bundle_impl,
    _rank_decision_memory_cards,
    _rank_improvement_note_cards,
    _rank_post_completion_critique_cards,
    _rank_strategy_outcome_cards,
    _render_decision_memory_cards,
    _render_improvement_note_cards,
    _render_post_completion_critique_cards,
    _render_strategy_outcome_cards,
    append_retrieval_segments as _append_retrieval_segments_impl,
)
from ..schemas import (
    ArtifactDigest,
    BuildConstraints,
    BuildPackRequest,
    ContextBudgets,
    ContextSegment,
    EvidenceItem,
    FactRecord,
    MemoryCard,
    PackingDecisionLog,
    RecentSessionArtifactRef,
    SessionSlice,
)
from .render import (
    _SegmentAssemblyRuntime,
    append_evidence_and_turn_input_segments as _append_evidence_and_turn_input_segments,
    append_prefix_and_mission_segments as _append_prefix_and_mission_segments,
    append_recent_window_segments as _append_recent_window_segments,
    append_summary_segments as _append_summary_segments,
    inject_context_drop_visibility_note,
    make_segment,
    map_turn_role,
    normalize_mode_name,
    protected_decide_recent_turn_indexes,
    render_context_drop_visibility_note,
    segments_to_messages,
)
from .trim import (
    LayoutDisciplineError,
    apply_trim_ladder,
    assert_layout_discipline,
    position_aware_v1,
)

__all__ = [
    "LayoutDisciplineError",
    "apply_trim_ladder",
    "assemble_segments",
    "assert_layout_discipline",
    "inject_context_drop_visibility_note",
    "make_segment",
    "map_turn_role",
    "normalize_mode_name",
    "position_aware_v1",
    "protected_decide_recent_turn_indexes",
    "render_context_drop_visibility_note",
    "segments_to_messages",
    "PackingDecisionLog",
    "_render_trailer_feedback",
    "_rank_decision_memory_cards",
    "_rank_improvement_note_cards",
    "_rank_post_completion_critique_cards",
    "_rank_strategy_outcome_cards",
    "_render_decision_memory_cards",
    "_render_improvement_note_cards",
    "_render_post_completion_critique_cards",
    "_render_strategy_outcome_cards",
]


def _filter_continuation_duplicate_memory_cards(
    memory_cards: list[MemoryCard],
    continuation: dict[str, Any] | None,
) -> list[MemoryCard]:
    event = continuation if isinstance(continuation, dict) else {}
    payload = (
        event.get("continuation") if isinstance(event.get("continuation"), dict) else {}
    )
    duplicate_ids = {
        str(item) for item in payload.get("memory_refs", []) if str(item).strip()
    }
    summary_ref = str(payload.get("session_work_summary_ref") or "").strip()
    if summary_ref:
        duplicate_ids.add(summary_ref)
    if not duplicate_ids:
        return memory_cards
    return [card for card in memory_cards if card.record_id not in duplicate_ids]


def assemble_segments(
    *,
    request: BuildPackRequest,
    constraints: BuildConstraints,
    prompt_tool_schemas: list[dict[str, Any]],
    budgets: ContextBudgets,
    bucket_caps: dict[str, int],
    identity_text: str,
    session_slice: SessionSlice,
    fact_records: list[FactRecord],
    memory_cards: list[MemoryCard],
    recalled_memory_cards: list[MemoryCard],
    recent_session_artifact_refs: list[RecentSessionArtifactRef],
    procedure: Any,
    skill_snippet_text: str | None,
    artifact_digests: list[ArtifactDigest],
    seed_text: str | None = None,
    rolling_enabled: bool,
    compression_enabled: bool,
    prefix_builder: Any,
    compressctl: Any | None,
    rlmctl: Any | None,
    vectorctl: Any | None,
    plugin_registry: Any,
    run_plugin_evidence_pipeline: Callable[..., list[EvidenceItem]],
    project_active_state_to_prompt_view: Callable[
        [dict[str, Any] | None], tuple[Any, dict[str, int]]
    ],
    build_clarify_digest: Callable[[dict[str, Any] | None], str],
    fit_to_budget: Callable[[str, int], tuple[str, bool]],
    estimate_tokens: Callable[[str], int],
    logger: Any | None = None,
) -> tuple[list[ContextSegment], dict[str, Any], dict[str, int]]:
    del recalled_memory_cards
    del bucket_caps
    runtime = _SegmentAssemblyRuntime(
        budgets=budgets,
        fit_to_budget=fit_to_budget,
        estimate_tokens=estimate_tokens,
    )
    _append_prefix_and_mission_segments(
        runtime,
        request=request,
        constraints=constraints,
        prompt_tool_schemas=prompt_tool_schemas,
        identity_text=identity_text,
        session_slice=session_slice,
        prefix_builder=prefix_builder,
        project_active_state_to_prompt_view=project_active_state_to_prompt_view,
        build_clarify_digest=build_clarify_digest,
        logger=logger,
    )
    _append_summary_segments(
        runtime,
        request=request,
        session_slice=session_slice,
        seed_text=seed_text,
        rolling_enabled=rolling_enabled,
        compression_enabled=compression_enabled,
        compressctl=compressctl,
    )
    _append_recent_window_segments(
        runtime,
        request=request,
        session_slice=session_slice,
    )
    retrieval_bundle = _collect_retrieval_bundle_impl(
        request=request,
        session_slice=session_slice,
        fact_records=fact_records,
        memory_cards=_filter_continuation_duplicate_memory_cards(
            memory_cards,
            session_slice.continuation,
        ),
        procedure=procedure,
        skill_snippet_text=skill_snippet_text,
        budgets=budgets,
        rlmctl=rlmctl,
        vectorctl=vectorctl,
    )
    _append_retrieval_segments_impl(
        runtime,
        constraints=constraints,
        bundle=retrieval_bundle,
        skill_snippet_text=skill_snippet_text,
        procedure=procedure,
    )
    _append_evidence_and_turn_input_segments(
        runtime,
        request=request,
        session_slice=session_slice,
        artifact_digests=artifact_digests,
        recent_session_artifact_refs=recent_session_artifact_refs,
        plugin_registry=plugin_registry,
        run_plugin_evidence_pipeline=run_plugin_evidence_pipeline,
    )
    return runtime.segments, runtime.bucket_stats, runtime.truncation_stats
