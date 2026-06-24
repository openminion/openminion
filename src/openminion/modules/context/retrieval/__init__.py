"""Compatibility facade for retrieval context assembly helpers."""

from .bundle import _SegmentAssemblyRetrievalBundle, collect_retrieval_bundle
from .cards import (
    _render_decision_memory_cards,
    _render_improvement_note_cards,
    _render_post_completion_critique_cards,
    _render_strategy_outcome_cards,
)
from .ranking import (
    _rank_decision_memory_cards,
    _rank_improvement_note_cards,
    _rank_post_completion_critique_cards,
    _rank_strategy_outcome_cards,
)
from .render import append_retrieval_segments

__all__ = [
    "_SegmentAssemblyRetrievalBundle",
    "_rank_decision_memory_cards",
    "_rank_improvement_note_cards",
    "_rank_post_completion_critique_cards",
    "_rank_strategy_outcome_cards",
    "_render_decision_memory_cards",
    "_render_improvement_note_cards",
    "_render_post_completion_critique_cards",
    "_render_strategy_outcome_cards",
    "append_retrieval_segments",
    "collect_retrieval_bundle",
]
