import json
from dataclasses import dataclass
from typing import Any, Callable, cast

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


@dataclass(frozen=True)
class _IncludedManifestInputs:
    facts: list[Any]
    memory: list[Any]
    session_start_recalled_memory: list[Any]
    mid_session_recalled_memory: list[Any]
    recent_session_artifacts: list[Any]
    procedure_id: str
    artifacts: list[ArtifactDigest]


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
    for included_segment in segments:
        if not included_segment.content.strip():
            continue
        segment_action = "pinned" if bool(included_segment.pinned) else "included"
        decisions.append(
            _decision_ref_for_segment(
                included_segment,
                action=segment_action,
                reason_code="pin_preserved" if included_segment.pinned else "selected",
            )
        )
        recorded_segment_actions.add((included_segment.id, segment_action))

    for trim_action in decision_log.actions:
        for segment_id in trim_action.segment_ids:
            matched_segment = segment_by_id.get(segment_id)
            if matched_segment is None:
                decisions.append(
                    ContextDecisionRef(
                        segment_id=str(segment_id),
                        bucket=str(trim_action.bucket or ""),
                        action=str(trim_action.action or "unknown"),
                        reason_code=str(trim_action.reason_code or "unknown"),
                        token_estimate=0,
                        source="decision_log",
                    )
                )
                continue
            normalized_action = (
                "truncated"
                if str(trim_action.action).startswith("shrink_")
                else str(trim_action.action or "unknown")
            )
            key = (matched_segment.id, normalized_action)
            if key in recorded_segment_actions:
                continue
            decisions.append(
                _decision_ref_for_segment(
                    matched_segment,
                    action=normalized_action,
                    reason_code=str(trim_action.reason_code or "unknown"),
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
        memory_block_refs=_refs_for_bucket(segments, "memory"),
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


@dataclass(frozen=True)
class _IncludedPackItems:
    all_segment_ids: list[str]
    included_segment_ids: list[str]
    dropped_segment_ids: list[str]
    facts: list[Any]
    memory: list[Any]
    session_start_recalled_memory: list[Any]
    mid_session_recalled_memory: list[Any]
    recent_session_artifacts: list[Any]
    procedure_id: str
    artifacts: list[ArtifactDigest]


def _has_segment_ref(segments: list[ContextSegment], ref: Any) -> bool:
    return any(ref in segment.refs for segment in segments)


def _included_pack_items(
    *,
    segments: list[ContextSegment],
    decision_log: PackingDecisionLog,
    fact_records: list[Any],
    memory_cards: list[Any],
    session_start_recalled_memory_cards: list[Any],
    recent_session_artifact_refs: list[Any],
    mid_session_recalled_memory_cards: list[Any],
    procedure: Any,
    artifact_digests: list[ArtifactDigest],
) -> _IncludedPackItems:
    return _IncludedPackItems(
        all_segment_ids=[segment.id for segment in segments],
        included_segment_ids=[segment.id for segment in segments if segment.content.strip()],
        dropped_segment_ids=list(
            dict.fromkeys(
                segment_id
                for action in decision_log.actions
                for segment_id in action.segment_ids
            )
        ),
        facts=[
            record
            for record in fact_records
            if record.ttl_valid and _has_segment_ref(segments, record.record_id)
        ],
        memory=[card for card in memory_cards if _has_segment_ref(segments, card.record_id)],
        session_start_recalled_memory=[
            card
            for card in session_start_recalled_memory_cards
            if _has_segment_ref(segments, card.record_id)
        ],
        mid_session_recalled_memory=[
            card
            for card in mid_session_recalled_memory_cards
            if _has_segment_ref(segments, card.record_id)
        ],
        recent_session_artifacts=[
            item
            for item in recent_session_artifact_refs
            if _has_segment_ref(segments, item.record_id)
        ],
        procedure_id=getattr(procedure, "procedure_id", "") if procedure else "",
        artifacts=[artifact for artifact in artifact_digests if _has_segment_ref(segments, artifact.ref)],
    )


def _build_context_manifest(
    *,
    request: BuildPackRequest,
    constraints: BuildConstraints,
    session_slice: SessionSlice,
    identity: Any,
    included: _IncludedPackItems,
    prompt_view: Any,
    projection_metrics: dict[str, Any],
    static_prefix_hash: str,
    prompt_cache_key: str,
    llm_call_id: str,
    plugin_registry: Any,
    normalize_context_budget_tier_fn: Callable[[Any], str | None],
    mid_session_recall_state: MidSessionRecallSnapshot | None,
) -> ContextManifest:
    return ContextManifest(
        identity=IdentityManifest(
            agent_id=request.agent_id,
            profile_version=identity.profile_version,
            render_version=identity.render_version,
        ),
        session=SessionManifest(
            slice_version=session_slice.slice_version,
            turn_index=int(session_slice.total_turn_count or len(session_slice.recent_turns)),
            turn_ids_included=[turn.turn_id for turn in session_slice.recent_turns],
        ),
        facts=[record.record_id for record in included.facts],
        memory=[card.record_id for card in included.memory],
        recalled_memory=[
            *[card.record_id for card in included.session_start_recalled_memory],
            *[card.record_id for card in included.mid_session_recalled_memory],
        ],
        session_start_recalled_memory=[card.record_id for card in included.session_start_recalled_memory],
        mid_session_recalled_memory=[card.record_id for card in included.mid_session_recalled_memory],
        recent_session_artifacts=[item.record_id for item in included.recent_session_artifacts],
        procedures=[included.procedure_id] if included.procedure_id else [],
        artifacts=[
            ArtifactManifestItem(ref=item.ref, view_id=item.view_id, digest_hash=item.digest_hash)
            for item in included.artifacts
        ],
        segment_ids=included.all_segment_ids,
        included_segment_ids=included.included_segment_ids,
        dropped_segment_ids=included.dropped_segment_ids,
        retrieval_summary=RetrievalSummary(),
        compression_summary=CompressionSummary(),
        static_prefix_hash=static_prefix_hash,
        prompt_cache_key=prompt_cache_key,
        prompt_context_id=session_slice.prompt_context_id,
        llm_call_id=llm_call_id,
        context_budget_tier=cast(
            Any, normalize_context_budget_tier_fn(constraints.context_budget_tier)
        ),
        pack_policy_used="position_aware_v1",
        retrievers_used=list(plugin_registry.retriever_names),
        compressors_used=list(plugin_registry.compressor_names),
        mid_session_recall_state=mid_session_recall_state,
        active_state_prompt_view=(prompt_view.model_dump() if hasattr(prompt_view, "model_dump") else {}),
        active_state_full=session_slice.active_state,
        active_state_metrics=projection_metrics,
    )


def _bucket_allocations(
    *,
    budgets: ContextBudgets,
    bucket_caps: dict[str, int],
    segments: list[ContextSegment],
    bucket_stats: dict[str, Any],
    decision_log: PackingDecisionLog,
) -> tuple[int, dict[str, BucketAllocation]]:
    total_used = sum(_estimate_tokens(segment.content) for segment in segments if segment.content.strip())
    allocations: dict[str, BucketAllocation] = {}
    for bucket, cap in bucket_caps.items():
        bucket_segments = [segment for segment in segments if segment.bucket == bucket]
        used = sum(
            _estimate_tokens(segment.content)
            for segment in bucket_segments
            if segment.content.strip()
        )
        stats = bucket_stats.get(bucket, {})
        allocations[bucket] = BucketAllocation(
            bucket=bucket,  # type: ignore[arg-type]
            cap_tokens=cap,
            used_tokens=used,
            selected_count=len([segment for segment in bucket_segments if segment.content.strip()]),
            total_available=stats.get("total_available", len(bucket_segments)),
            dropped_count=stats.get("dropped", 0),
            trim_applied=(
                any(action.bucket == bucket for action in decision_log.actions)
                or int(stats.get("truncated", 0) or 0) > 0
                or bool(stats.get("budget_exceeded", False))
            ),
        )
    return total_used, allocations


def _token_budget_report(
    *,
    budgets: ContextBudgets,
    total_used: int,
    bucket_allocs: dict[str, BucketAllocation],
    decision_log: PackingDecisionLog,
) -> TokenBudgetReport:
    return TokenBudgetReport(
        total_cap_tokens=budgets.total_max_tokens,
        total_used_tokens=total_used,
        buckets=bucket_allocs,
        total_dropped_segments=sum(allocation.dropped_count for allocation in bucket_allocs.values()),
        over_budget=total_used > budgets.total_max_tokens,
        degrade_trace=[action.action + ":" + action.reason_code for action in decision_log.actions],
        decision_log=decision_log,
    )


def _pack_version(
    *,
    request: BuildPackRequest,
    constraints: BuildConstraints,
    budgets: ContextBudgets,
    identity_budget: IdentityBudgetResult,
    session_slice: SessionSlice,
    manifest: ContextManifest,
) -> str:
    return _stable_hash(
        {
            "identity_text": identity_budget.text,
            "slice_version": session_slice.slice_version,
            "facts": manifest.facts,
            "memory": manifest.memory,
            "recalled_memory": manifest.recalled_memory,
            "session_start_recalled_memory": manifest.session_start_recalled_memory,
            "mid_session_recalled_memory": manifest.mid_session_recalled_memory,
            "mid_session_recall_state": (
                manifest.mid_session_recall_state.model_dump()
                if manifest.mid_session_recall_state is not None
                else None
            ),
            "artifacts": [item.model_dump() for item in manifest.artifacts],
            "purpose": request.purpose,
            "query": request.query,
            "constraints": constraints.model_dump(),
            "phase_hints": request.phase_hints,
            "live_state_overlay": request.live_state_overlay,
            "budgets": budgets.model_dump(),
        }
    )


def _included_manifest_inputs(
    *,
    segments: list[ContextSegment],
    fact_records: list[Any],
    memory_cards: list[Any],
    session_start_recalled_memory_cards: list[Any],
    mid_session_recalled_memory_cards: list[Any],
    recent_session_artifact_refs: list[Any],
    procedure: Any,
    artifact_digests: list[ArtifactDigest],
) -> _IncludedManifestInputs:
    def segment_has_ref(ref: str) -> bool:
        return any(ref in segment.refs for segment in segments)

    return _IncludedManifestInputs(
        facts=[
            fact
            for fact in fact_records
            if fact.ttl_valid and segment_has_ref(fact.record_id)
        ],
        memory=[card for card in memory_cards if segment_has_ref(card.record_id)],
        session_start_recalled_memory=[
            card
            for card in session_start_recalled_memory_cards
            if segment_has_ref(card.record_id)
        ],
        mid_session_recalled_memory=[
            card
            for card in mid_session_recalled_memory_cards
            if segment_has_ref(card.record_id)
        ],
        recent_session_artifacts=[
            item
            for item in recent_session_artifact_refs
            if segment_has_ref(item.record_id)
        ],
        procedure_id=getattr(procedure, "procedure_id", "") if procedure else "",
        artifacts=[
            artifact for artifact in artifact_digests if segment_has_ref(artifact.ref)
        ],
    )


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
    included = _included_pack_items(
        segments=segments,
        decision_log=decision_log,
        fact_records=fact_records,
        memory_cards=memory_cards,
        session_start_recalled_memory_cards=session_start_recalled_memory_cards,
        recent_session_artifact_refs=recent_session_artifact_refs,
        mid_session_recalled_memory_cards=mid_session_recalled_memory_cards,
        procedure=procedure,
        artifact_digests=artifact_digests,
    )
    manifest = _build_context_manifest(
        request=request,
        constraints=constraints,
        session_slice=session_slice,
        identity=identity,
        included=included,
        prompt_view=prompt_view,
        projection_metrics=projection_metrics,
        static_prefix_hash=static_prefix_hash,
        prompt_cache_key=prompt_cache_key,
        llm_call_id=llm_call_id,
        plugin_registry=plugin_registry,
        normalize_context_budget_tier_fn=normalize_context_budget_tier_fn,
        mid_session_recall_state=mid_session_recall_state,
    )
    total_used, bucket_allocs = _bucket_allocations(
        budgets=budgets,
        bucket_caps=bucket_caps,
        segments=segments,
        bucket_stats=bucket_stats,
        decision_log=decision_log,
    )
    token_report = _token_budget_report(
        budgets=budgets,
        total_used=total_used,
        bucket_allocs=bucket_allocs,
        decision_log=decision_log,
    )
    pack_version = _pack_version(
        request=request,
        constraints=constraints,
        budgets=budgets,
        identity_budget=identity_budget,
        session_slice=session_slice,
        manifest=manifest,
    )
    manifest.decision_trace = build_context_decision_trace(
        session_id=request.session_id,
        turn_id=llm_call_id,
        llm_call_id=llm_call_id,
        prompt_context_id=session_slice.prompt_context_id,
        pack_version=pack_version,
        segments=segments,
        decision_log=decision_log,
        token_budget_report=token_report,
    )
    return FinalizedPackResult(
        pack=ContextPack(
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
            context_manifest=manifest,
            token_budget_report=token_report,
            pack_policy=decision_log,
            warnings=list(warnings),
            prompt_context_id=session_slice.prompt_context_id,
            seed_bundle_id=session_slice.seed_bundle_id,
        ),
        drop_count=len(included.dropped_segment_ids),
        truncation_count=sum(truncation_stats.values()),
    )
