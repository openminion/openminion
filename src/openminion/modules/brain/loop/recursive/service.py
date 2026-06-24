from typing import Any

from .contracts import (
    ArtifactClient,
    CompressionClient,
    ContextClient,
    LLMClient,
    MemoryClient,
    RLM_CONTRACT_VERSION,
    RetrievalClient,
    SessionClient,
    SkillClient,
)
from .schemas import RLMConfig


class RLMService:
    """Recursive LLM wrapper with retrieval gating and selective augmentation."""

    contract_version = RLM_CONTRACT_VERSION

    def __init__(
        self,
        *,
        sessctl: SessionClient,
        contextctl: ContextClient | None = None,
        llmctl: LLMClient,
        artifactctl: ArtifactClient | None = None,
        memctl: MemoryClient | None = None,
        skillctl: SkillClient | None = None,
        retrievectl: RetrievalClient | None = None,
        compressctl: CompressionClient | None = None,
        config: RLMConfig | dict[str, Any] | None = None,
    ) -> None:
        self._sessctl = sessctl
        if contextctl is None:
            raise TypeError(
                "RLMService.__init__() missing required argument: 'contextctl'"
            )
        self._contextctl = contextctl
        self._llmctl = llmctl
        self._artifactctl = artifactctl
        self._memctl = memctl
        self._skillctl = skillctl
        self._retrievectl = retrievectl
        self._compressctl = compressctl
        self.config = (
            config
            if isinstance(config, RLMConfig)
            else RLMConfig.model_validate(config or {})
        )

    from .generation import generate
    from .memory import (
        refresh_working_memory,
        _append_event,
        _build_episode_markdown,
        _estimate_citation_coverage,
        _infer_ref_type,
        _load_wm_state,
        _merge_evidence_refs,
        _merge_wm,
        _normalize_evidence_refs,
        _normalize_raptor_level,
        _normalize_ref_type,
        _normalize_source,
        _normalize_strategy,
        _normalize_unit_kind,
        _safe_get_slice,
        _save_wm_state,
        _stage_memory_candidates,
        _to_plain,
        _trust_score_from_source,
        _validate_wm_state,
        _write_episode_note,
    )
    from .retrieval import (
        expand,
        retrieve,
        _alternate_strategy,
        _artifact_ref_from_meta,
        _artifact_ref_to_text,
        _artifact_text_excerpt,
        _evaluate_retrieval_quality,
        _extract_tags,
        _filter_retrieval_items,
        _keyword_score,
        _meta_to_searchable_text,
        _normalize_retrieval_rows,
        _normalize_semantic_rows,
        _recency_score,
        _resolve_retrieval_strategy,
        _retrieve_episodic,
        _retrieve_external,
        _retrieve_local,
        _retrieve_semantic,
        _retrieve_skills,
        _score_histogram,
    )
    from .llm import (
        _build_tick_messages,
        _call_llm,
        _compress_blocks,
        _compress_with_external,
        _extract_json_dict,
        _extract_pack_hash,
        _extract_usage,
        _normalize_messages,
        _parse_tick_output,
        _pick_ensemble_candidate,
        _run_awaitable,
    )
