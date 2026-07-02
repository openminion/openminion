"""Retrieval bundle collection for segment assembly."""

from dataclasses import dataclass, field as _dc_field
from typing import Any

from ..config import (
    CONTEXT_IMPROVEMENT_NOTE_LIMIT,
    CONTEXT_POST_COMPLETION_CRITIQUE_LIMIT,
    CONTEXT_STRATEGY_OUTCOME_LIMIT,
)
from ..constants import DECISION_MEMORY_LIMIT
from ..mode_ranking import (
    _MODE_ACT,
    _MODE_PLAN,
    _MODE_RESPOND,
    normalize_mode_name,
    rank_memory_cards_for_mode as _mode_rank_memory_cards,
)
from ..schemas import (
    BuildPackRequest,
    ContextBudgets,
    FactRecord,
    MemoryCard,
    SessionSlice,
)

from .overlay import (
    _cards_by_record_type,
    _cards_from_overlay,
    _extend_unique_record_cards,
    _low_progress_signal_payload,
    _state_decision_memory_cards,
)
from .ranking import (
    _is_operational_tool_failure_item,
    _rank_decision_memory_cards,
    _rank_improvement_note_cards,
    _rank_post_completion_critique_cards,
    _rank_strategy_outcome_cards,
)


@dataclass
class _SegmentAssemblyRetrievalBundle:
    rlm_summary: str | None = None
    vector_results: list[tuple[str, float, dict[str, Any]]] = _dc_field(
        default_factory=list
    )
    capped_facts: list[FactRecord] = _dc_field(default_factory=list)
    capped_memory: list[MemoryCard] = _dc_field(default_factory=list)
    capped_decision_memory: list[MemoryCard] = _dc_field(default_factory=list)
    capped_improvement_notes: list[MemoryCard] = _dc_field(default_factory=list)
    capped_strategy_outcomes: list[MemoryCard] = _dc_field(default_factory=list)
    capped_post_completion_critiques: list[MemoryCard] = _dc_field(default_factory=list)
    retrieval_total: int = 0
    low_progress_signal: dict[str, Any] = _dc_field(default_factory=dict)


def _fetch_rlm_summary(
    *,
    request: BuildPackRequest,
    rlmctl: Any | None,
) -> str | None:
    if rlmctl is None:
        return None
    try:
        return rlmctl.get_refresh_summary(
            session_id=request.session_id,
            agent_id=request.agent_id,
            query=request.query or request.purpose,
        )
    except Exception:
        return None


def _fetch_vector_results(
    *,
    request: BuildPackRequest,
    vectorctl: Any | None,
) -> list[tuple[str, float, dict[str, Any]]]:
    if vectorctl is None or not request.query:
        return []
    try:
        return vectorctl.search(
            query=request.query,
            top_k=5,
            filters={"session_id": request.session_id} if request.session_id else None,
        )
    except Exception:
        return []


def _fact_memory_limits(mode_name: str | None) -> tuple[int, int]:
    if mode_name == _MODE_RESPOND:
        return 12, 6
    if mode_name == _MODE_PLAN:
        return 10, 15
    if mode_name == _MODE_ACT:
        return 10, 10
    return 20, 15


def _cap_facts(fact_records: list[FactRecord], fact_limit: int) -> list[FactRecord]:
    valid_facts = [
        fact
        for fact in fact_records
        if fact.ttl_valid and not _is_operational_tool_failure_item(fact)
    ]
    return [
        fact.model_copy(update={"text": fact.text[:200]})
        if len(fact.text) > 200
        else fact
        for fact in valid_facts[:fact_limit]
    ]


def _cap_general_memory(
    valid_memory_cards: list[MemoryCard],
    *,
    mode_name: str | None,
    memory_limit: int,
) -> list[MemoryCard]:
    special_types = {
        "decision",
        "improvement_note",
        "strategy_outcome",
        "post_completion_critique",
    }
    candidates = [
        item
        for item in valid_memory_cards
        if str(item.record_type or "").strip().lower() not in special_types
    ]
    return [
        item.model_copy(update={"text": item.text[:250]})
        if len(item.text) > 250
        else item
        for item in _mode_rank_memory_cards(candidates, mode_name=mode_name)[
            :memory_limit
        ]
    ]


def collect_retrieval_bundle(
    *,
    request: BuildPackRequest,
    session_slice: SessionSlice,
    fact_records: list[FactRecord],
    memory_cards: list[MemoryCard],
    procedure: Any,
    skill_snippet_text: str | None,
    budgets: ContextBudgets,
    rlmctl: Any | None,
    vectorctl: Any | None,
) -> _SegmentAssemblyRetrievalBundle:
    del budgets
    mode_name = normalize_mode_name(request.mode_name)
    fact_limit, memory_limit = _fact_memory_limits(mode_name)
    capped_facts = _cap_facts(fact_records, fact_limit)
    valid_memory_cards = [
        item for item in memory_cards if not _is_operational_tool_failure_item(item)
    ]
    decision_memory_cards = _extend_unique_record_cards(
        _cards_by_record_type(valid_memory_cards, "decision"),
        _state_decision_memory_cards(request=request, session_slice=session_slice),
    )
    improvement_note_cards = _extend_unique_record_cards(
        _cards_by_record_type(valid_memory_cards, "improvement_note"),
        _cards_from_overlay(
            request=request,
            session_slice=session_slice,
            overlay_key="improvement_note_cards",
            record_type="improvement_note",
            default_text="improvement_note_ref",
        ),
    )
    strategy_outcome_cards = _extend_unique_record_cards(
        _cards_by_record_type(valid_memory_cards, "strategy_outcome"),
        _cards_from_overlay(
            request=request,
            session_slice=session_slice,
            overlay_key="strategy_outcome_cards",
            record_type="strategy_outcome",
            default_text="strategy_outcome_ref",
        ),
    )
    post_completion_critique_cards = _extend_unique_record_cards(
        _cards_by_record_type(valid_memory_cards, "post_completion_critique"),
        _cards_from_overlay(
            request=request,
            session_slice=session_slice,
            overlay_key="post_completion_critique_cards",
            record_type="post_completion_critique",
            default_text="post_completion_critique_ref",
        ),
    )
    capped_decision_memory: list[MemoryCard] = []
    capped_improvement_notes: list[MemoryCard] = []
    capped_strategy_outcomes: list[MemoryCard] = []
    capped_post_completion_critiques: list[MemoryCard] = []
    if request.purpose == "decide":
        capped_decision_memory = _rank_decision_memory_cards(
            decision_memory_cards,
            request=request,
        )[:DECISION_MEMORY_LIMIT]
        capped_improvement_notes = _rank_improvement_note_cards(
            improvement_note_cards,
            request=request,
        )[:CONTEXT_IMPROVEMENT_NOTE_LIMIT]
        capped_strategy_outcomes = _rank_strategy_outcome_cards(
            strategy_outcome_cards,
            request=request,
        )[:CONTEXT_STRATEGY_OUTCOME_LIMIT]
        capped_post_completion_critiques = _rank_post_completion_critique_cards(
            post_completion_critique_cards,
            request=request,
        )[:CONTEXT_POST_COMPLETION_CRITIQUE_LIMIT]
    capped_memory = _cap_general_memory(
        valid_memory_cards,
        mode_name=mode_name,
        memory_limit=memory_limit,
    )
    rlm_summary = _fetch_rlm_summary(request=request, rlmctl=rlmctl)
    vector_results = _fetch_vector_results(request=request, vectorctl=vectorctl)
    retrieval_total = (
        len(capped_facts)
        + len(capped_memory)
        + len(capped_decision_memory)
        + len(capped_improvement_notes)
        + len(capped_strategy_outcomes)
        + len(capped_post_completion_critiques)
        + (1 if rlm_summary else 0)
        + len(vector_results)
    )
    if skill_snippet_text or procedure:
        retrieval_total += 1
    return _SegmentAssemblyRetrievalBundle(
        rlm_summary=rlm_summary,
        vector_results=vector_results,
        capped_facts=capped_facts,
        capped_memory=capped_memory,
        capped_decision_memory=capped_decision_memory,
        capped_improvement_notes=capped_improvement_notes,
        capped_strategy_outcomes=capped_strategy_outcomes,
        capped_post_completion_critiques=capped_post_completion_critiques,
        retrieval_total=retrieval_total,
        low_progress_signal=_low_progress_signal_payload(
            request=request,
            session_slice=session_slice,
        ),
    )
