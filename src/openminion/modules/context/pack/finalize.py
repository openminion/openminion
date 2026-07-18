import json
from dataclasses import dataclass
from typing import Any, Callable

from ..constants import CONTEXT_DROP_VISIBILITY_BUCKETS
from .identity import IdentityBudgetResult
from .budgeting import _content_hash, _estimate_tokens
from ..schemas import (
    ArtifactDigest,
    ArtifactManifestItem,
    BucketAllocation,
    BuildConstraints,
    BuildPackRequest,
    CompressionSummary,
    ContextBudgets,
    ContextDecisionRef,
    ContextDecisionTraceV1,
    ContextManifest,
    ContextPack,
    ContextSegment,
    IdentityManifest,
    MidSessionRecallSnapshot,
    PackingDecisionLog,
    RetrievalSummary,
    SessionManifest,
    SessionSlice,
    TokenBudgetReport,
    _stable_hash,
)
from openminion.base.constants import STATE_KEY_ACTIVE


@dataclass(frozen=True)
class FinalizedPackResult:
    pack: ContextPack
    drop_count: int
    truncation_count: int


def context_drop_visibility_counts(
    *,
    decision_log: PackingDecisionLog,
    bucket_stats: dict[str, Any],
) -> dict[str, int]:
    """Return structural omitted-candidate counts by typed context bucket."""

    counts: dict[str, int] = dict.fromkeys(CONTEXT_DROP_VISIBILITY_BUCKETS, 0)
    for bucket in CONTEXT_DROP_VISIBILITY_BUCKETS:
        dropped = bucket_stats.get(bucket, {}).get("dropped", 0)
        try:
            counts[bucket] += max(0, int(dropped))
        except (TypeError, ValueError):
            continue

    for action in decision_log.actions:
        if action.action != "drop_segment":
            continue
        bucket = str(action.bucket or "")
        if bucket not in counts:
            continue
        counts[bucket] += max(1, len(action.segment_ids))

    return {bucket: count for bucket, count in counts.items() if count > 0}


def apply_live_state_overlay(
    *,
    session_slice: SessionSlice,
    live_state_overlay: dict[str, Any] | None,
) -> SessionSlice:
    if not live_state_overlay:
        return session_slice

    merged_active_state = (
        dict(session_slice.active_state)
        if isinstance(session_slice.active_state, dict)
        else {}
    )
    for key, value in live_state_overlay.items():
        if value is None:
            continue
        merged_active_state[key] = value
    return session_slice.model_copy(update={STATE_KEY_ACTIVE: merged_active_state})


def build_runtime_cache_lookup_key(
    *,
    request: BuildPackRequest,
    session_slice: SessionSlice,
    profile_version: str,
) -> tuple[str, ...]:
    query_hash = _stable_hash(request.query)
    constraints_hash = _stable_hash(
        request.constraints.model_dump() if request.constraints else {}
    )
    phase_hints_hash = _stable_hash(request.phase_hints)
    budgets_hash = _stable_hash(
        request.budgets_override.model_dump() if request.budgets_override else {}
    )
    budget_telemetry_hash = _stable_hash(request.budget_telemetry or {})
    return (
        request.session_id,
        request.agent_id,
        request.purpose,
        request.mode_name or "",
        query_hash,
        session_slice.last_event_id or "",
        profile_version,
        request.provider_pref or "",
        request.model_hint or "",
        constraints_hash,
        phase_hints_hash,
        budgets_hash,
        budget_telemetry_hash,
    )


def build_prompt_cache_key(
    *,
    prefix_builder: Any,
    prefix_cache_adapter: Any,
    request: BuildPackRequest,
    constraints: BuildConstraints,
    segments: list[ContextSegment],
    prompt_tool_schemas: list[dict[str, Any]],
) -> tuple[str, str]:
    static_segs = [s for s in segments if s.bucket == "static_prefix"]
    static_prefix_text = "".join(s.content for s in static_segs)
    static_prefix_hash = prefix_builder.hash(static_prefix_text)
    tool_schema_hash = _content_hash(
        json.dumps(
            {
                "output_schema": constraints.output_schema or {},
                "prompt_tools": prompt_tool_schemas,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
    )
    policy_hash = _content_hash(str(sorted(constraints.safety_tags)))
    if prefix_cache_adapter is not None:
        prompt_cache_key = prefix_cache_adapter.build_cache_key(
            agent_id=request.agent_id,
            static_prefix_hash=static_prefix_hash,
            tool_schema_hash=tool_schema_hash,
            policy_hash=policy_hash,
            model_hint=request.model_hint or "",
        )
    else:
        prompt_cache_key = _stable_hash(
            {
                "model": request.model_hint or "",
                "agent_id": request.agent_id,
                "static_prefix_hash": static_prefix_hash,
                "tool_schema_hash": tool_schema_hash,
                "policy_hash": policy_hash,
            }
        )
    return static_prefix_hash, prompt_cache_key


def build_context_decision_trace(
    *,
    session_id: str,
    turn_id: str | None,
    llm_call_id: str | None,
    prompt_context_id: str | None,
    pack_version: str,
    segments: list[ContextSegment],
    decision_log: PackingDecisionLog,
    token_budget_report: TokenBudgetReport,
) -> ContextDecisionTraceV1:
    segment_by_id = {segment.id: segment for segment in segments}
    decisions: list[ContextDecisionRef] = []
    recorded_segment_actions: set[tuple[str, str]] = set()
    for segment in segments:
        if not segment.content.strip():
            continue
        action = "pinned" if bool(segment.pinned) else "included"
        decisions.append(
            _decision_ref_for_segment(
                segment,
                action=action,
                reason_code="pin_preserved" if segment.pinned else "selected",
            )
        )
        recorded_segment_actions.add((segment.id, action))

    for action in decision_log.actions:
        for segment_id in action.segment_ids:
            segment = segment_by_id.get(segment_id)
            if segment is None:
                decisions.append(
                    ContextDecisionRef(
                        segment_id=str(segment_id),
                        bucket=str(action.bucket or ""),
                        action=str(action.action or "unknown"),
                        reason_code=str(action.reason_code or "unknown"),
                        token_estimate=0,
                        source="decision_log",
                    )
                )
                continue
            normalized_action = (
                "truncated"
                if str(action.action).startswith("shrink_")
                else str(action.action or "unknown")
            )
            key = (segment.id, normalized_action)
            if key in recorded_segment_actions:
                continue
            decisions.append(
                _decision_ref_for_segment(
                    segment,
                    action=normalized_action,
                    reason_code=str(action.reason_code or "unknown"),
                    source="decision_log",
                )
            )
            recorded_segment_actions.add(key)

    missing_sources = []
    if not any(segment.bucket == "retrieval" for segment in segments):
        missing_sources.append("retrieval_unavailable")
    if not any(segment.refs for segment in segments):
        missing_sources.append("provenance_refs_unavailable")

    trace = ContextDecisionTraceV1(
        session_id=session_id,
        turn_id=turn_id,
        llm_call_id=llm_call_id,
        prompt_context_id=prompt_context_id,
        pack_version=pack_version,
        decisions=decisions,
        token_budget_report=token_budget_report,
        retrieval_score_refs=_refs_for_bucket(segments, "retrieval"),
        memory_provenance_refs=_refs_for_bucket(segments, "retrieval"),
        summary_checkpoint_refs=_summary_checkpoint_refs(segments),
        missing_sources=missing_sources,
    )
    return trace.bounded()


def _decision_ref_for_segment(
    segment: ContextSegment,
    *,
    action: str,
    reason_code: str,
    source: str = "typed_schema",
) -> ContextDecisionRef:
    digest = segment.content_hash or _content_hash(segment.content)
    return ContextDecisionRef(
        segment_id=segment.id,
        bucket=segment.bucket,
        action=action,
        reason_code=reason_code,
        token_estimate=max(0, int(segment.token_estimate or 0)),
        content_digest=digest,
        refs=list(segment.refs),
        source=source,
    )


def _refs_for_bucket(segments: list[ContextSegment], bucket: str) -> list[str]:
    refs: list[str] = []
    for segment in segments:
        if segment.bucket != bucket:
            continue
        refs.extend(str(ref) for ref in segment.refs if str(ref).strip())
    return list(dict.fromkeys(refs))


def _summary_checkpoint_refs(segments: list[ContextSegment]) -> list[str]:
    refs: list[str] = []
    for segment in segments:
        if segment.bucket not in {"summaries", "conversation_summary", "task_digest"}:
            continue
        refs.extend(str(ref) for ref in segment.refs if str(ref).strip())
    return list(dict.fromkeys(refs))


def finalize_context_pack(
    *,
    request: BuildPackRequest,
    constraints: BuildConstraints,
    budgets: ContextBudgets,
    bucket_caps: dict[str, int],
    session_slice: SessionSlice,
    segments: list[ContextSegment],
    fact_records: list[Any],
    memory_cards: list[Any],
    session_start_recalled_memory_cards: list[Any],
    recent_session_artifact_refs: list[Any],
    mid_session_recalled_memory_cards: list[Any],
    procedure: Any,
    artifact_digests: list[ArtifactDigest],
    identity: Any,
    identity_budget: IdentityBudgetResult,
    prompt_tool_schemas: list[dict[str, Any]],
    decision_log: PackingDecisionLog,
    bucket_stats: dict[str, Any],
    truncation_stats: dict[str, int],
    warnings: list[str],
    llm_call_id: str,
    prefix_builder: Any,
    prefix_cache_adapter: Any,
    plugin_registry: Any,
    segments_to_messages_fn: Callable[[list[ContextSegment]], list[Any]],
    project_active_state_to_prompt_view_fn: Callable[[Any], tuple[Any, dict[str, Any]]],
    normalize_context_budget_tier_fn: Callable[[Any], str | None],
    mid_session_recall_state: MidSessionRecallSnapshot | None,
) -> FinalizedPackResult:
    prompt_view, projection_metrics = project_active_state_to_prompt_view_fn(
        session_slice.active_state
    )
    static_prefix_hash, prompt_cache_key = build_prompt_cache_key(
        prefix_builder=prefix_builder,
        prefix_cache_adapter=prefix_cache_adapter,
        request=request,
        constraints=constraints,
        segments=segments,
        prompt_tool_schemas=prompt_tool_schemas,
    )
    messages = segments_to_messages_fn(segments)

    all_seg_ids = [s.id for s in segments]
    included_seg_ids = [s.id for s in segments if s.content.strip()]
    dropped_seg_ids = list(
        dict.fromkeys(
            sid for action in decision_log.actions for sid in action.segment_ids
        )
    )
    drop_count = len(dropped_seg_ids)
    truncation_count = sum(truncation_stats.values())

    included_facts = [
        f
        for f in fact_records
        if f.ttl_valid and any(f.record_id in s.refs for s in segments)
    ]
    included_memory = [
        m for m in memory_cards if any(m.record_id in s.refs for s in segments)
    ]
    included_session_start_recalled_memory = [
        m
        for m in session_start_recalled_memory_cards
        if any(m.record_id in s.refs for s in segments)
    ]
    included_mid_session_recalled_memory = [
        m
        for m in mid_session_recalled_memory_cards
        if any(m.record_id in s.refs for s in segments)
    ]
    included_recent_session_artifacts = [
        item
        for item in recent_session_artifact_refs
        if any(item.record_id in s.refs for s in segments)
    ]
    included_procedure = getattr(procedure, "procedure_id", "") if procedure else ""
    included_artifacts = [
        a for a in artifact_digests if any(a.ref in s.refs for s in segments)
    ]

    context_manifest = ContextManifest(
        identity=IdentityManifest(
            agent_id=request.agent_id,
            profile_version=identity.profile_version,
            render_version=identity.render_version,
        ),
        session=SessionManifest(
            slice_version=session_slice.slice_version,
            turn_index=int(
                session_slice.total_turn_count or len(session_slice.recent_turns)
            ),
            turn_ids_included=[t.turn_id for t in session_slice.recent_turns],
        ),
        facts=[f.record_id for f in included_facts],
        memory=[m.record_id for m in included_memory],
        recalled_memory=[
            *[m.record_id for m in included_session_start_recalled_memory],
            *[m.record_id for m in included_mid_session_recalled_memory],
        ],
        session_start_recalled_memory=[
            m.record_id for m in included_session_start_recalled_memory
        ],
        mid_session_recalled_memory=[
            m.record_id for m in included_mid_session_recalled_memory
        ],
        recent_session_artifacts=[
            item.record_id for item in included_recent_session_artifacts
        ],
        procedures=[included_procedure] if included_procedure else [],
        artifacts=[
            ArtifactManifestItem(
                ref=a.ref, view_id=a.view_id, digest_hash=a.digest_hash
            )
            for a in included_artifacts
        ],
        segment_ids=all_seg_ids,
        included_segment_ids=included_seg_ids,
        dropped_segment_ids=dropped_seg_ids,
        retrieval_summary=RetrievalSummary(),
        compression_summary=CompressionSummary(),
        static_prefix_hash=static_prefix_hash,
        prompt_cache_key=prompt_cache_key,
        prompt_context_id=session_slice.prompt_context_id,
        llm_call_id=llm_call_id,
        context_budget_tier=normalize_context_budget_tier_fn(
            constraints.context_budget_tier
        ),
        pack_policy_used="position_aware_v1",
        retrievers_used=list(plugin_registry.retriever_names),
        compressors_used=list(plugin_registry.compressor_names),
        mid_session_recall_state=mid_session_recall_state,
        active_state_prompt_view=(
            prompt_view.model_dump() if hasattr(prompt_view, "model_dump") else {}
        ),
        active_state_full=session_slice.active_state,
        active_state_metrics=projection_metrics,
    )

    total_used = sum(_estimate_tokens(s.content) for s in segments if s.content.strip())
    bucket_allocs = {}
    for bkt, cap in bucket_caps.items():
        bkt_segs = [s for s in segments if s.bucket == bkt]
        used = sum(_estimate_tokens(s.content) for s in bkt_segs if s.content.strip())
        total_avail = bucket_stats.get(bkt, {}).get("total_available", len(bkt_segs))
        dropped = bucket_stats.get(bkt, {}).get("dropped", 0)
        bucket_allocs[bkt] = BucketAllocation(
            bucket=bkt,  # type: ignore[arg-type]
            cap_tokens=cap,
            used_tokens=used,
            selected_count=len([s for s in bkt_segs if s.content.strip()]),
            total_available=total_avail,
            dropped_count=dropped,
            trim_applied=any(a.bucket == bkt for a in decision_log.actions),
        )

    token_budget_report = TokenBudgetReport(
        total_cap_tokens=budgets.total_max_tokens,
        total_used_tokens=total_used,
        buckets=bucket_allocs,
        total_dropped_segments=sum(a.dropped_count for a in bucket_allocs.values()),
        over_budget=total_used > budgets.total_max_tokens,
        degrade_trace=[a.action + ":" + a.reason_code for a in decision_log.actions],
        decision_log=decision_log,
    )

    pack_version = _stable_hash(
        {
            "identity_text": identity_budget.text,
            "slice_version": session_slice.slice_version,
            "facts": context_manifest.facts,
            "memory": context_manifest.memory,
            "recalled_memory": context_manifest.recalled_memory,
            "session_start_recalled_memory": (
                context_manifest.session_start_recalled_memory
            ),
            "mid_session_recalled_memory": context_manifest.mid_session_recalled_memory,
            "mid_session_recall_state": (
                context_manifest.mid_session_recall_state.model_dump()
                if context_manifest.mid_session_recall_state is not None
                else None
            ),
            "artifacts": [a.model_dump() for a in context_manifest.artifacts],
            "purpose": request.purpose,
            "query": request.query,
            "constraints": constraints.model_dump(),
            "phase_hints": request.phase_hints,
            "live_state_overlay": request.live_state_overlay,
            "budgets": budgets.model_dump(),
        }
    )
    context_manifest.decision_trace = build_context_decision_trace(
        session_id=request.session_id,
        turn_id=llm_call_id,
        llm_call_id=llm_call_id,
        prompt_context_id=session_slice.prompt_context_id,
        pack_version=pack_version,
        segments=segments,
        decision_log=decision_log,
        token_budget_report=token_budget_report,
    )

    pack = ContextPack(
        session_id=request.session_id,
        agent_id=request.agent_id,
        purpose=request.purpose,
        segments=segments,
        messages=messages,
        profile_version=identity.profile_version,
        render_version=identity.render_version,
        slice_version=session_slice.slice_version,
        pack_version=pack_version,
        pack_hash=pack_version,
        prompt_cache_key=prompt_cache_key,
        static_prefix_hash=static_prefix_hash,
        context_manifest=context_manifest,
        token_budget_report=token_budget_report,
        pack_policy=decision_log,
        warnings=list(warnings),
        prompt_context_id=session_slice.prompt_context_id,
        seed_bundle_id=session_slice.seed_bundle_id,
    )
    return FinalizedPackResult(
        pack=pack,
        drop_count=drop_count,
        truncation_count=truncation_count,
    )
