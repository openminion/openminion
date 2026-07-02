from dataclasses import dataclass
import logging
from typing import Any
from uuid import uuid4

from openminion.base.config.env import resolve_environment_config
from openminion.modules.tool.schema_service import ToolSchemaService

from .state.projection import (
    _build_clarify_digest,
    _project_active_state_to_prompt_view,
)
from .contracts import (
    ArtifactClient,
    CompressClient,
    IdentityClient,
    MemoryClient,
    PluginRegistry,
    RlmClient,
    SessionClient,
    SkillClient,
    VectorClient,
    ensure_context_client_compatibility,
)
from .pack.identity import (
    IdentityBudgetResult as _IdentityBudgetResult,
    ResolvedIdentityBudgetConfig as _ResolvedIdentityBudgetConfig,
    apply_identity_budget as _apply_identity_budget_impl,
    resolve_identity_budget_config as _resolve_identity_budget_config_impl,
)
from .pack.budgeting import (
    _apply_context_budget_tier_bias,
    _apply_mode_budget_bias,
    _estimate_tokens,
    _fit_to_budget,
    _normalize_context_budget_tier,
)
from .pack.finalize import (
    apply_live_state_overlay as _apply_live_state_overlay_impl,
    build_runtime_cache_lookup_key as _build_runtime_cache_lookup_key_impl,
    context_drop_visibility_counts as _context_drop_visibility_counts_impl,
    finalize_context_pack as _finalize_context_pack_impl,
)
from .prefix import PinnedPrefixBuilder, PrefixCacheAdapter
from .render.sections import (
    estimate_tokens as _estimate_tokens_messages_impl,
    render_artifact_digest as _render_artifact_digest_impl,
    render_fact_table as _render_fact_table_impl,
    render_memory_cards as _render_memory_cards_impl,
    render_procedure_snippet as _render_procedure_snippet_impl,
)
from .constants import (
    OPENMINION_STRICT_CONTEXT_CONTRACTS_ENV,
)
from .schemas import (
    ArtifactDigest,
    BuildConstraints,
    BuildPackRequest,
    ContextBudgets,
    ContextManifest,
    ContextPack,
    ContextSegment,
    EvidenceItem,
    FactRecord,
    MemoryCard,
    RecentSessionArtifactRef,
    MidSessionRecallSnapshot,
    PackingDecisionLog,
    RenderMessage,
    SessionSlice,
    TokenReport,
    bucket_caps_for,
    decide_budget_for_turn_depth,
    default_budgets_for,
)
from .retrieval.materials import (
    RetrievedContextMaterialsCollector,
    _RetrievedContextMaterials,
    _dedupe_memory_cards,
)
from .segment import (
    LayoutDisciplineError as _LayoutDisciplineError,
    apply_trim_ladder as _apply_trim_ladder_impl,
    assemble_segments as _assemble_segments_impl,
    assert_layout_discipline as _assert_layout_discipline_impl,
    inject_context_drop_visibility_note as _inject_context_drop_visibility_note_impl,
    make_segment as _make_segment_impl,
    position_aware_v1 as _position_aware_v1_impl,
    segments_to_messages as _segments_to_messages_impl,
)
from .summary.state import ContextSummaryState, SummaryDelta
from .telemetry import ContextTelemetryBridge
from .compress.eligibility import (
    CompactionBudgetState,
    CompactionEligibility,
    DefaultCompactionEligibility,
    EligibilityResult,
)
from .config import load_config as _load_context_module_config

_logger = logging.getLogger(__name__)
_MODULE_ID = "openminion-context"
_TOOL_SCHEMA_SERVICE = ToolSchemaService()
LayoutDisciplineError = _LayoutDisciplineError
_WORKING_STATE_MODULE_STATE_ATTR = "".join(("module", "_", "state"))
_MAINTENANCE_MODULE_STATE_KEY = "".join(("memory", "_", "context", "_", "maintenance"))


class IdentityMissingError(RuntimeError):
    pass


class MissionContextMissingError(RuntimeError):
    """Raised when the mission_snapshot bucket would be empty (fail-closed)."""


@dataclass(frozen=True)
class _BuildPackRuntimeState:
    llm_call_id: str
    constraints: BuildConstraints
    session_slice: SessionSlice
    budgets: ContextBudgets
    prompt_tool_schemas: list[dict[str, Any]]
    bucket_caps: dict[str, int]
    cache_allowed: bool
    identity: Any
    identity_budget: _IdentityBudgetResult
    cache_key: tuple[str, ...]


def _make_segment(
    seg_id: str,
    bucket: str,
    content: str,
    *,
    role: str = "system",
    refs: list[str] | None = None,
    is_artifact_preview: bool = False,
    pinned: bool = False,
) -> ContextSegment:
    return _make_segment_impl(
        seg_id,
        bucket,
        content,
        role=role,
        refs=refs,
        is_artifact_preview=is_artifact_preview,
        pinned=pinned,
        estimate_tokens=_estimate_tokens,
    )


def _position_aware_v1(
    segments: list[ContextSegment], scores: list[float]
) -> list[ContextSegment]:
    return _position_aware_v1_impl(segments, scores)


def _assert_layout_discipline(segments: list[ContextSegment]) -> None:
    _assert_layout_discipline_impl(segments)


class ContextCtlService:
    """V1.5 ContextCtlService: builds segment-first ContextPacks."""

    def __init__(
        self,
        *,
        identityctl: IdentityClient,
        sessctl: SessionClient,
        memctl: MemoryClient,
        artifactctl: ArtifactClient,
        skillctl: SkillClient | None = None,
        compressctl: CompressClient | None = None,
        rlmctl: RlmClient | None = None,
        vectorctl: VectorClient | None = None,
        vector_adapter: Any = None,
        prefix_builder: PinnedPrefixBuilder | None = None,
        plugin_registry: PluginRegistry | None = None,
        prefix_cache_adapter: PrefixCacheAdapter | None = None,
        telemetryctl: Any | None = None,
        identity_budget: Any | None = None,
        rolling_enabled: bool = True,
        compaction_enabled: bool = True,
        compression_enabled: bool = True,
    ) -> None:
        self._identityctl = identityctl
        self._sessctl = sessctl
        self._memctl = memctl
        self._artifactctl = artifactctl
        self._skillctl = skillctl
        self._compressctl = compressctl
        self._rlmctl = rlmctl
        self._vectorctl = vectorctl
        self._vector_adapter = vector_adapter
        self._prefix_builder = prefix_builder or PinnedPrefixBuilder()
        self._plugin_registry = plugin_registry or PluginRegistry()
        self._prefix_cache_adapter = prefix_cache_adapter
        self._identity_budget_cfg = self._resolve_identity_budget_config(
            identity_budget
        )
        self._rolling_enabled = bool(rolling_enabled)
        self._compaction_enabled = bool(compaction_enabled)
        self._compression_enabled = bool(compression_enabled)
        self._context_module_config = _load_context_module_config()
        self._compaction_eligibility: CompactionEligibility = (
            DefaultCompactionEligibility(
                compaction_trigger_percent=(
                    self._context_module_config.compaction_trigger_percent
                )
            )
        )
        self._summary_state = ContextSummaryState(enabled=self._compaction_enabled)
        self._telemetry = ContextTelemetryBridge(
            sessctl=self._sessctl,
            telemetryctl=telemetryctl,
            logger=_logger,
            module_id=_MODULE_ID,
        )
        self._cache: dict[tuple[str, ...], ContextPack] = {}
        self._manifest_index: dict[str, ContextManifest] = {}
        self._latest_manifest_by_session: dict[str, ContextManifest] = {}
        self._retrieved_materials = RetrievedContextMaterialsCollector(self)
        self._validate_client_contracts()

    def _retrieved_materials_helper(self) -> RetrievedContextMaterialsCollector:
        helper = getattr(self, "_retrieved_materials", None)
        if helper is None:
            helper = RetrievedContextMaterialsCollector(self)
            self._retrieved_materials = helper
        return helper

    def _validate_client_contracts(self) -> None:
        strict = str(
            resolve_environment_config().get(
                OPENMINION_STRICT_CONTEXT_CONTRACTS_ENV, "1"
            )
        ).strip().lower() not in {"0", "false", "no", "off"}
        checks: list[tuple[str, Any]] = [
            ("identity", self._identityctl),
            ("session", self._sessctl),
            ("memory", self._memctl),
            ("artifact", self._artifactctl),
        ]
        if self._skillctl is not None:
            checks.append(("skill", self._skillctl))
        if self._compressctl is not None:
            checks.append(("compress", self._compressctl))
        if self._rlmctl is not None:
            checks.append(("rlm", self._rlmctl))
        if self._vectorctl is not None:
            checks.append(("vector", self._vectorctl))

        for client_type, client in checks:
            try:
                ensure_context_client_compatibility(client, client_type=client_type)
            except Exception as exc:  # noqa: BLE001
                message = (
                    f"context client contract check failed for {client_type}: {exc}"
                )
                if strict:
                    raise RuntimeError(message) from exc
                _logger.warning("%s", message)

    def build_pack(self, request: BuildPackRequest) -> ContextPack:
        runtime_state = self._prepare_build_pack_runtime_state(request)
        if runtime_state.cache_allowed and runtime_state.cache_key in self._cache:
            return self._return_cached_pack(
                request=request,
                runtime_state=runtime_state,
            )

        materials = self._collect_retrieved_context_materials(
            request=request,
            constraints=runtime_state.constraints,
            budgets=runtime_state.budgets,
            session_slice=runtime_state.session_slice,
        )

        assembly_constraints = runtime_state.constraints.model_copy(
            update={"skill_id": materials.skill_segment_id}
        )
        segments, bucket_stats, truncation_stats = self._assemble_segments(
            request=request,
            constraints=assembly_constraints,
            prompt_tool_schemas=runtime_state.prompt_tool_schemas,
            budgets=runtime_state.budgets,
            bucket_caps=runtime_state.bucket_caps,
            identity_text=runtime_state.identity_budget.text,
            session_slice=runtime_state.session_slice,
            fact_records=materials.fact_records,
            memory_cards=materials.memory_cards,
            session_start_recalled_memory_cards=materials.session_start_recalled_memory_cards,
            recent_session_artifact_refs=materials.recent_session_artifact_refs,
            mid_session_recalled_memory_cards=materials.mid_session_recalled_memory_cards,
            procedure=materials.procedure,
            skill_snippet_text=materials.skill_snippet_text,
            artifact_digests=materials.artifact_digests,
        )

        decision_log = PackingDecisionLog()
        warnings: list[str] = []
        segments, decision_log, warnings = self._apply_trim_ladder(
            segments=segments,
            total_cap=runtime_state.budgets.total_max_tokens,
            bucket_caps=runtime_state.bucket_caps,
            decision_log=decision_log,
            warnings=warnings,
        )
        segments = _inject_context_drop_visibility_note_impl(
            segments=segments,
            drop_counts=_context_drop_visibility_counts_impl(
                decision_log=decision_log,
                bucket_stats=bucket_stats,
            ),
            estimate_tokens=_estimate_tokens,
        )

        self._apply_evidence_priority_ordering(
            segments=segments,
            artifact_digests=materials.artifact_digests,
        )

        _assert_layout_discipline(segments)

        finalized = _finalize_context_pack_impl(
            request=request,
            constraints=runtime_state.constraints,
            budgets=runtime_state.budgets,
            bucket_caps=runtime_state.bucket_caps,
            session_slice=runtime_state.session_slice,
            segments=segments,
            fact_records=materials.fact_records,
            memory_cards=materials.memory_cards,
            session_start_recalled_memory_cards=materials.session_start_recalled_memory_cards,
            recent_session_artifact_refs=materials.recent_session_artifact_refs,
            mid_session_recalled_memory_cards=materials.mid_session_recalled_memory_cards,
            procedure=materials.procedure,
            artifact_digests=materials.artifact_digests,
            identity=runtime_state.identity,
            identity_budget=runtime_state.identity_budget,
            prompt_tool_schemas=runtime_state.prompt_tool_schemas,
            decision_log=decision_log,
            bucket_stats=bucket_stats,
            truncation_stats=truncation_stats,
            warnings=warnings,
            llm_call_id=runtime_state.llm_call_id,
            prefix_builder=self._prefix_builder,
            prefix_cache_adapter=self._prefix_cache_adapter,
            plugin_registry=self._plugin_registry,
            segments_to_messages_fn=self._segments_to_messages,
            project_active_state_to_prompt_view_fn=_project_active_state_to_prompt_view,
            normalize_context_budget_tier_fn=_normalize_context_budget_tier,
            mid_session_recall_state=materials.mid_session_recall_state,
        )
        pack = finalized.pack

        self._record_built_pack(
            request=request,
            runtime_state=runtime_state,
            pack=pack,
            drop_count=finalized.drop_count,
            truncation_count=finalized.truncation_count,
        )
        return pack

    def _prepare_build_pack_runtime_state(
        self, request: BuildPackRequest
    ) -> _BuildPackRuntimeState:
        llm_call_id = request.llm_call_id or uuid4().hex
        constraints = request.constraints or BuildConstraints()
        session_slice = self._sessctl.get_slice(
            session_id=request.session_id,
            purpose=request.purpose,
            limits={"max_turns": 12, "max_tool_events": 3},
        )
        session_slice = _apply_live_state_overlay_impl(
            session_slice=session_slice,
            live_state_overlay=request.live_state_overlay,
        )
        session_turn_count = int(
            session_slice.total_turn_count or len(session_slice.recent_turns)
        )
        budgets = self._resolve_budgets(
            request.purpose,
            request.budgets_override,
            turn_count=session_turn_count,
        )
        budgets = _apply_mode_budget_bias(budgets, mode_name=request.mode_name)
        budgets = _apply_context_budget_tier_bias(
            budgets,
            tier=constraints.context_budget_tier,
        )
        prompt_tool_schemas = _TOOL_SCHEMA_SERVICE.build_prompt_tool_schemas(
            query=request.query,
            tool_schemas=constraints.tool_schemas,
        )
        bucket_caps = bucket_caps_for(budgets)
        identity = self._render_identity(
            agent_id=request.agent_id,
            purpose=request.purpose,
            max_tokens=budgets.identity_tokens,
            provider_pref=request.provider_pref,
            query_text=request.query,
        )
        if not identity.text.strip():
            raise IdentityMissingError("IDENTITY_MISSING")
        identity_budget = self._apply_identity_budget(
            identity=identity, budgets=budgets
        )
        return _BuildPackRuntimeState(
            llm_call_id=llm_call_id,
            constraints=constraints,
            session_slice=session_slice,
            budgets=budgets,
            prompt_tool_schemas=prompt_tool_schemas,
            bucket_caps=bucket_caps,
            cache_allowed=not bool(request.live_state_overlay),
            identity=identity,
            identity_budget=identity_budget,
            cache_key=_build_runtime_cache_lookup_key_impl(
                request=request,
                session_slice=session_slice,
                profile_version=identity.profile_version,
            ),
        )

    def _render_identity(
        self,
        *,
        agent_id: str,
        purpose: str,
        max_tokens: int,
        provider_pref: str | None,
        query_text: str,
    ) -> Any:
        render = self._identityctl.render
        try:
            return render(
                agent_id=agent_id,
                purpose=purpose,
                max_tokens=max_tokens,
                provider_pref=provider_pref,
                query_text=query_text,
            )
        except TypeError as exc:
            if "query_text" not in str(exc):
                raise
            return render(
                agent_id=agent_id,
                purpose=purpose,
                max_tokens=max_tokens,
                provider_pref=provider_pref,
            )

    def _return_cached_pack(
        self,
        *,
        request: BuildPackRequest,
        runtime_state: _BuildPackRuntimeState,
    ) -> ContextPack:
        cached_pack = self._cache[runtime_state.cache_key]
        self._latest_manifest_by_session[request.session_id] = (
            cached_pack.context_manifest
        )
        self._telemetry.emit_identity_audit_events(
            session_id=request.session_id,
            agent_id=request.agent_id,
            purpose=request.purpose,
            profile_version=runtime_state.identity.profile_version,
            render_version=runtime_state.identity.render_version,
        )
        self._telemetry.emit_pack_manifest_event(
            session_id=request.session_id,
            agent_id=request.agent_id,
            pack=cached_pack,
            cache_hit=True,
            llm_call_id=runtime_state.llm_call_id,
        )
        cached_drop_count = (
            len(cached_pack.context_manifest.dropped_segment_ids)
            if cached_pack.context_manifest
            else 0
        )
        self._telemetry.emit_pack_module_telemetry(
            session_id=request.session_id,
            turn_id=runtime_state.llm_call_id,
            pack=cached_pack,
            drop_count=cached_drop_count,
            truncation_count=0,
            cache_hit=True,
            mode=request.mode_name,
        )
        return cached_pack

    def _collect_retrieved_context_materials(
        self,
        *,
        request: BuildPackRequest,
        constraints: BuildConstraints,
        budgets: ContextBudgets,
        session_slice: SessionSlice,
    ) -> _RetrievedContextMaterials:
        return self._retrieved_materials_helper().collect_retrieved_context_materials(
            request=request,
            constraints=constraints,
            budgets=budgets,
            session_slice=session_slice,
        )

    def _resolve_skill_snippet(
        self,
        *,
        constraints: BuildConstraints,
        purpose: str,
        mode_name: str | None,
        skills_tokens: int,
    ) -> tuple[str | None, str | None]:
        return self._retrieved_materials_helper().resolve_skill_snippet(
            constraints=constraints,
            purpose=purpose,
            mode_name=mode_name,
            skills_tokens=skills_tokens,
        )

    def _apply_evidence_priority_ordering(
        self,
        *,
        segments: list[ContextSegment],
        artifact_digests: list[ArtifactDigest],
    ) -> None:
        ev_segs = [
            (i, s)
            for i, s in enumerate(segments)
            if s.bucket == "evidence_refs" and s.content.strip()
        ]
        if len(ev_segs) <= 1:
            return
        idxs = [i for i, _ in ev_segs]
        slist = [s for _, s in ev_segs]
        ref_to_score = {a.ref: a.score for a in artifact_digests}
        scores = [ref_to_score.get(s.refs[0], 0.5) if s.refs else 0.0 for s in slist]
        ordered_ev = _position_aware_v1(slist, scores)
        for orig_idx, new_seg in zip(idxs, ordered_ev):
            segments[orig_idx] = new_seg

    def _record_built_pack(
        self,
        *,
        request: BuildPackRequest,
        runtime_state: _BuildPackRuntimeState,
        pack: ContextPack,
        drop_count: int,
        truncation_count: int,
    ) -> None:
        if runtime_state.cache_allowed:
            self._cache[runtime_state.cache_key] = pack
        self._manifest_index[pack.pack_version] = pack.context_manifest
        self._latest_manifest_by_session[request.session_id] = pack.context_manifest
        self._telemetry.emit_identity_audit_events(
            session_id=request.session_id,
            agent_id=request.agent_id,
            purpose=request.purpose,
            profile_version=runtime_state.identity.profile_version,
            render_version=runtime_state.identity.render_version,
        )
        self._telemetry.emit_pack_manifest_event(
            session_id=request.session_id,
            agent_id=request.agent_id,
            pack=pack,
            cache_hit=False,
            llm_call_id=runtime_state.llm_call_id,
        )
        self._telemetry.emit_pack_module_telemetry(
            session_id=request.session_id,
            turn_id=runtime_state.llm_call_id,
            pack=pack,
            drop_count=drop_count,
            truncation_count=truncation_count,
            cache_hit=False,
            mode=request.mode_name,
        )
        self._clear_surfaced_trailer_feedback(
            pack=pack,
            session_id=request.session_id,
            agent_id=request.agent_id,
        )

    def _clear_surfaced_trailer_feedback(
        self,
        *,
        pack: ContextPack,
        session_id: str,
        agent_id: str,
    ) -> None:
        """Emit trailer.feedback_surfaced when the pack surfaced feedback."""
        feedback_surfaced = False
        for segment in getattr(pack, "segments", []) or []:
            if getattr(segment, "bucket", "") == "trailer_feedback":
                content = str(getattr(segment, "content", "") or "").strip()
                if content:
                    feedback_surfaced = True
                    break
        if not feedback_surfaced:
            return
        append_event = getattr(self._sessctl, "append_event", None)
        if not callable(append_event):
            store = getattr(self._sessctl, "_store", None) or getattr(
                self._sessctl, "store", None
            )
            append_event = getattr(store, "append_event", None) if store else None
        if not callable(append_event):
            return
        try:
            append_event(
                session_id,
                "trailer.feedback_surfaced",
                {"route": "decide"},
                actor_type="system",
                actor_id=agent_id,
                importance=1,
                redaction="none",
                status="ok",
            )
        except Exception:  # noqa: BLE001
            return

    def _recall_session_start_memory(
        self,
        *,
        request: BuildPackRequest,
        turn_index: int,
    ) -> list[MemoryCard]:
        return self._retrieved_materials_helper().recall_session_start_memory(
            request=request,
            turn_index=turn_index,
        )

    def _build_mid_session_recall_state(
        self,
        *,
        session_slice: SessionSlice,
        turn_index: int,
    ) -> MidSessionRecallSnapshot:
        return self._retrieved_materials_helper().build_mid_session_recall_state(
            session_slice=session_slice,
            turn_index=turn_index,
        )

    def _recall_recent_session_artifacts(
        self,
        *,
        request: BuildPackRequest,
        turn_index: int,
    ) -> list[RecentSessionArtifactRef]:
        return self._retrieved_materials_helper().recall_recent_session_artifacts(
            request=request,
            turn_index=turn_index,
        )

    def _should_recall_mid_session_memory(
        self,
        *,
        session_slice: SessionSlice,
        recall_state: MidSessionRecallSnapshot,
        prior_manifest: ContextManifest | None,
    ) -> bool:
        return self._retrieved_materials_helper()._should_recall_mid_session_memory(
            session_slice=session_slice,
            recall_state=recall_state,
            prior_manifest=prior_manifest,
        )

    def _recall_mid_session_memory(
        self,
        *,
        request: BuildPackRequest,
        session_slice: SessionSlice,
        recall_state: MidSessionRecallSnapshot,
        prior_manifest: ContextManifest | None,
    ) -> list[MemoryCard]:
        return self._retrieved_materials_helper().recall_mid_session_memory(
            request=request,
            session_slice=session_slice,
            recall_state=recall_state,
            prior_manifest=prior_manifest,
        )

    def record_cache_metrics(
        self,
        *,
        session_id: str,
        agent_id: str,
        prompt_cache_key: str,
        cached_tokens: int,
        total_tokens: int,
        provider: str,
    ) -> None:
        self._telemetry.record_cache_metrics(
            session_id=session_id,
            agent_id=agent_id,
            prompt_cache_key=prompt_cache_key,
            cached_tokens=cached_tokens,
            total_tokens=total_tokens,
            provider=provider,
        )

    def _assemble_segments(
        self,
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
        session_start_recalled_memory_cards: list[MemoryCard],
        recent_session_artifact_refs: list[RecentSessionArtifactRef],
        mid_session_recalled_memory_cards: list[MemoryCard],
        procedure: Any,
        skill_snippet_text: str | None = None,
        artifact_digests: list[ArtifactDigest],
        seed_text: str | None = None,
    ) -> tuple[list[ContextSegment], dict[str, Any], dict[str, int]]:
        try:
            return _assemble_segments_impl(
                request=request,
                constraints=constraints,
                prompt_tool_schemas=prompt_tool_schemas,
                budgets=budgets,
                bucket_caps=bucket_caps,
                identity_text=identity_text,
                session_slice=session_slice,
                fact_records=fact_records,
                memory_cards=memory_cards,
                recalled_memory_cards=_dedupe_memory_cards(
                    [
                        *session_start_recalled_memory_cards,
                        *mid_session_recalled_memory_cards,
                    ]
                ),
                recent_session_artifact_refs=recent_session_artifact_refs,
                procedure=procedure,
                skill_snippet_text=skill_snippet_text,
                artifact_digests=artifact_digests,
                seed_text=seed_text,
                rolling_enabled=self._rolling_enabled,
                compression_enabled=self._compression_enabled,
                prefix_builder=self._prefix_builder,
                compressctl=self._compressctl,
                rlmctl=self._rlmctl,
                vectorctl=self._vectorctl,
                plugin_registry=self._plugin_registry,
                run_plugin_evidence_pipeline=self._run_plugin_evidence_pipeline,
                project_active_state_to_prompt_view=_project_active_state_to_prompt_view,
                build_clarify_digest=_build_clarify_digest,
                fit_to_budget=_fit_to_budget,
                estimate_tokens=_estimate_tokens,
                logger=_logger,
            )
        except RuntimeError as exc:
            if str(exc) == "MISSION_CONTEXT_MISSING":
                raise MissionContextMissingError("MISSION_CONTEXT_MISSING") from exc
            raise

    def _run_plugin_evidence_pipeline(
        self,
        request: BuildPackRequest,
        *,
        query: str,
        k: int,
    ) -> list[EvidenceItem]:
        """Call registered retrievers then apply each compressor in sequence."""
        all_items: list[EvidenceItem] = []
        for name in self._plugin_registry.retriever_names:
            retriever = self._plugin_registry.get_retriever(name)
            if retriever is None:
                continue
            try:
                items = retriever.retrieve(
                    session_id=request.session_id,
                    query=query,
                    k=k,
                    filters={},
                )
                all_items.extend(items)
            except Exception:
                pass
        for name in self._plugin_registry.compressor_names:
            compressor = self._plugin_registry.get_compressor(name)
            if compressor is None:
                continue
            try:
                all_items = compressor.compress(
                    query=query,
                    items=all_items,
                    budget_tokens=k * 120,
                )
            except Exception:
                pass
        return all_items

    def _apply_trim_ladder(
        self,
        segments: list[ContextSegment],
        total_cap: int,
        bucket_caps: dict[str, int],
        decision_log: PackingDecisionLog,
        warnings: list[str],
    ) -> tuple[list[ContextSegment], PackingDecisionLog, list[str]]:
        return _apply_trim_ladder_impl(
            segments,
            total_cap,
            bucket_caps,
            decision_log,
            warnings,
            estimate_tokens=_estimate_tokens,
        )

    def _segments_to_messages(
        self, segments: list[ContextSegment]
    ) -> list[RenderMessage]:
        return _segments_to_messages_impl(segments)

    def make_delta(
        self, *, session_id: str, agent_id: str, content: str
    ) -> SummaryDelta:
        return self._summary_state.make_delta(
            session_id=session_id,
            agent_id=agent_id,
            content=content,
        )

    def maybe_compact(self, session_id: str, *, threshold: int = 5) -> bool:
        return self._summary_state.maybe_compact(session_id, threshold=threshold)

    def evaluate_self_compaction_eligibility(
        self,
        *,
        working_state: Any,
        prompt_token_estimate: int,
        budget_state: CompactionBudgetState,
        now: Any,
    ) -> EligibilityResult:
        return self._compaction_eligibility.is_eligible(
            working_state,
            prompt_token_estimate=prompt_token_estimate,
            budget_state=budget_state,
            now=now,
        )

    def maybe_compact_with_state(
        self,
        session_id: str,
        *,
        working_state: Any | None = None,
        threshold: int = 5,
    ) -> bool:
        if self._should_skip_passive_compaction(working_state=working_state):
            return False
        return self._summary_state.maybe_compact(session_id, threshold=threshold)

    def _should_skip_passive_compaction(self, *, working_state: Any | None) -> bool:
        if working_state is None:
            return False
        module_state = getattr(working_state, _WORKING_STATE_MODULE_STATE_ATTR, None)
        if not isinstance(module_state, dict):
            return False
        maintenance = module_state.get(_MAINTENANCE_MODULE_STATE_KEY)
        if not isinstance(maintenance, dict):
            return False
        return bool(str(maintenance.get("last_compaction_marker", "") or "").strip())

    def get_summary_base(self, session_id: str) -> str | None:
        return self._summary_state.get_summary_base(session_id)

    def get_summary_deltas(self, session_id: str) -> list[SummaryDelta]:
        return self._summary_state.get_summary_deltas(session_id)

    def render_fact_table(self, facts: list[FactRecord], max_tokens: int) -> str:
        return _render_fact_table_impl(
            facts,
            max_tokens,
            fit_to_budget=_fit_to_budget,
        )

    def render_memory_cards(self, records: list[MemoryCard], max_tokens: int) -> str:
        return _render_memory_cards_impl(
            records,
            max_tokens,
            fit_to_budget=_fit_to_budget,
        )

    def render_artifact_digest(self, digest: ArtifactDigest, max_tokens: int) -> str:
        return _render_artifact_digest_impl(
            digest,
            max_tokens,
            fit_to_budget=_fit_to_budget,
        )

    def render_procedure_snippet(self, proc: Any, max_tokens: int) -> str:
        return _render_procedure_snippet_impl(
            proc,
            max_tokens,
            fit_to_budget=_fit_to_budget,
        )

    def estimate_tokens(self, messages: list[RenderMessage]) -> TokenReport:
        return _estimate_tokens_messages_impl(
            messages,
            estimate_text_tokens=_estimate_tokens,
        )

    def explain_pack(self, pack_version: str) -> ContextManifest | None:
        return self._manifest_index.get(pack_version)

    def _resolve_identity_budget_config(
        self,
        payload: Any | None,
    ) -> _ResolvedIdentityBudgetConfig | None:
        return _resolve_identity_budget_config_impl(payload)

    def _apply_identity_budget(
        self,
        *,
        identity: Any,
        budgets: ContextBudgets,
    ) -> _IdentityBudgetResult:
        cap_tokens = max(1, int(budgets.identity_tokens))
        return _apply_identity_budget_impl(
            identity=identity,
            cap_tokens=cap_tokens,
            cfg=self._identity_budget_cfg,
            fit_to_budget=_fit_to_budget,
            estimate_tokens=_estimate_tokens,
        )

    def _resolve_budgets(
        self,
        purpose: str,
        override: ContextBudgets | None,
        *,
        turn_count: int = 0,
    ) -> ContextBudgets:
        if purpose == "decide" and turn_count > 0:
            base = decide_budget_for_turn_depth(turn_count)
        else:
            base = default_budgets_for(purpose)  # type: ignore[arg-type]
        if override is None:
            return base
        data = base.model_dump()
        data.update(override.model_dump(exclude_unset=True))
        return ContextBudgets(**data)
