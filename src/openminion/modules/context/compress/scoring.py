from dataclasses import dataclass
from typing import Iterable, Sequence

from .schemas import InputBlock

TRUST_TIER_VALUES: dict[str, float] = {
    "platinum": 1.0,
    "gold": 0.9,
    "silver": 0.75,
    "bronze": 0.5,
}

TYPE_PRIOR: dict[str, float] = {
    "retrieval": 1.0,
    "dialogue": 0.8,
    "memory": 0.7,
    "skill": 0.6,
    "wm": 0.6,
    "episode": 0.65,
    "episode_condensate": 0.65,
}


@dataclass(frozen=True)
class ScoredUnit:
    """Single scored unit derived from an input block."""

    block_id: str
    block_type: str
    text: str
    refs: Sequence[str]
    score: float
    trust_value: float
    retrieval_score: float
    source_id: str
    source_index: int
    unit_offset: int

    def sort_key(self) -> tuple:
        return (
            -self.score,
            -self.trust_value,
            -self.retrieval_score,
            self.source_index,
            self.unit_offset,
        )


def build_scored_units(blocks: Iterable[InputBlock], query: str) -> list[ScoredUnit]:
    scored: list[ScoredUnit] = []
    for index, block in enumerate(blocks):
        trust = TRUST_TIER_VALUES.get(
            str(block.meta.get("trust_tier", "")).lower(), 0.5
        )
        retrieval = float(block.meta.get("retrieval_score", 0.0))
        type_prior = TYPE_PRIOR.get(block.type, 0.5)
        overlap = _lexical_overlap(query, block.text)
        recency = _normalize_recency(block.meta.get("recency_ts"))
        score = (
            0.45 * retrieval
            + 0.25 * trust
            + 0.15 * type_prior
            + 0.10 * overlap
            + 0.05 * recency
        )
        scored.append(
            ScoredUnit(
                block_id=block.block_id,
                block_type=block.type,
                text=block.text,
                refs=list(block.refs),
                score=round(score, 6),
                trust_value=trust,
                retrieval_score=retrieval,
                source_id=str(block.meta.get("source_id", block.block_id)),
                source_index=int(block.meta.get("source_index", index)),
                unit_offset=index,
            )
        )
    scored.sort(key=lambda unit: unit.sort_key())
    return scored


def _lexical_overlap(query: str, text: str) -> float:
    query_terms = _tokenize(query)
    text_terms = _tokenize(text)
    if not query_terms or not text_terms:
        return 0.0
    overlap = len(query_terms & text_terms)
    return overlap / max(len(query_terms), 1)


def _tokenize(payload: str) -> set[str]:
    return {token.strip().lower() for token in payload.split() if token.strip()}


def _normalize_recency(recency_ts) -> float:
    if not recency_ts:
        return 0.0
    try:
        value = float(recency_ts)
    except (TypeError, ValueError):
        return 0.0
    return min(max(value / 1_000_000_000.0, 0.0), 1.0)
