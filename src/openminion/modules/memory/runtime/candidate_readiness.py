from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from openminion.modules.memory.models import MemoryCandidate
from openminion.modules.memory.runtime.scorer import (
    clamp01,
    outcome_utility_signal_from_meta,
)
from openminion.base.time import utc_now as _utc_now
from openminion.base.constants import STATE_KEY_SOURCE_OUTCOME
from ..errors import InvalidArgumentError

_SUCCESS_OUTCOME_STATUSES = {"success", "succeeded", "pass", "passed", "improved"}
_NEGATIVE_OUTCOME_STATUSES = {"failed", "failure", "timeout", "timed_out", "error"}


def _coerce_dt(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True)
class PromotionWeights:
    reconfirmation: float
    retrieval_hits: float
    survival: float
    confidence: float
    correction_resistance: float
    outcome_utility: float = 0.20

    def __post_init__(self) -> None:
        total = sum(
            float(value)
            for value in (
                self.reconfirmation,
                self.retrieval_hits,
                self.survival,
                self.confidence,
                self.correction_resistance,
                self.outcome_utility,
            )
        )
        if total <= 0.0:
            raise InvalidArgumentError("promotion weights must sum to a positive value")


@dataclass(frozen=True)
class CandidateSignalVector:
    reconfirmation: float
    retrieval_hits: float
    survival: float
    confidence: float
    correction_resistance: float
    outcome_utility: float


def _candidate_outcome_utility(meta: dict[str, Any]) -> float:
    observed_utility = outcome_utility_signal_from_meta(meta)
    has_explicit_outcome_counts = bool(
        int(meta.get("outcome_success_count", 0) or 0)
        or int(meta.get("outcome_failure_count", 0) or 0)
    )
    if has_explicit_outcome_counts:
        return observed_utility
    raw_status = (
        str(
            meta.get(STATE_KEY_SOURCE_OUTCOME, meta.get("last_outcome_status", ""))
            or ""
        )
        .strip()
        .lower()
    )
    if bool(meta.get("source_success_path")) or raw_status in _SUCCESS_OUTCOME_STATUSES:
        return 0.75
    if (
        bool(meta.get("source_negative_outcome"))
        or raw_status in _NEGATIVE_OUTCOME_STATUSES
    ):
        return 0.25
    return 0.5


def compute_promotion_readiness(
    reconfirmation: float,
    retrieval_hits: float,
    survival: float,
    confidence: float,
    correction_resistance: float,
    outcome_utility: float = 0.5,
    trust_score: float = 1.0,
    *,
    weights: PromotionWeights,
) -> float:
    weighted_sum = sum(
        float(weight) * clamp01(value)
        for weight, value in (
            (weights.reconfirmation, reconfirmation),
            (weights.retrieval_hits, retrieval_hits),
            (weights.survival, survival),
            (weights.confidence, confidence),
            (weights.correction_resistance, correction_resistance),
            (weights.outcome_utility, outcome_utility),
        )
    )
    return clamp01(weighted_sum * clamp01(trust_score))


def extract_candidate_signals(
    candidate: MemoryCandidate,
    *,
    now: datetime | str | None = None,
    reconfirmation_target: int = 2,
    retrieval_hit_target: int = 3,
    survival_halflife_days: float = 7.0,
) -> CandidateSignalVector:
    meta = dict(getattr(candidate, "meta", {}) or {})
    reconfirmation_count = max(0.0, float(meta.get("reconfirmation_count", 0) or 0))
    retrieval_hit_count = max(0.0, float(meta.get("retrieval_hit_count", 0) or 0))
    contradicted = bool(meta.get("contradicted", False))
    created_at = _coerce_dt(getattr(candidate, "created_at", None))
    effective_now = _coerce_dt(now) or _utc_now()

    if created_at is None:
        survival_signal = 0.0
    else:
        age_days = max(0.0, (effective_now - created_at).total_seconds() / 86400.0)
        survival_signal = 1.0 - 0.5 ** (
            age_days / max(float(survival_halflife_days), 0.001)
        )

    return CandidateSignalVector(
        reconfirmation=clamp01(
            reconfirmation_count / max(float(reconfirmation_target), 1.0)
        ),
        retrieval_hits=clamp01(
            retrieval_hit_count / max(float(retrieval_hit_target), 1.0)
        ),
        survival=clamp01(survival_signal),
        confidence=clamp01(float(getattr(candidate, "confidence", 0.0) or 0.0)),
        correction_resistance=0.0 if contradicted else 1.0,
        outcome_utility=_candidate_outcome_utility(meta),
    )


def score_candidate(
    candidate: MemoryCandidate,
    *,
    weights: PromotionWeights,
    now: datetime | str | None = None,
    reconfirmation_target: int = 2,
    retrieval_hit_target: int = 3,
    survival_halflife_days: float = 7.0,
    trust_score: float = 1.0,
) -> float:
    signals = extract_candidate_signals(
        candidate,
        now=now,
        reconfirmation_target=reconfirmation_target,
        retrieval_hit_target=retrieval_hit_target,
        survival_halflife_days=survival_halflife_days,
    )
    return compute_promotion_readiness(
        signals.reconfirmation,
        signals.retrieval_hits,
        signals.survival,
        signals.confidence,
        signals.correction_resistance,
        signals.outcome_utility,
        trust_score,
        weights=weights,
    )


def score_candidate_from_config(
    candidate: MemoryCandidate,
    *,
    config: Any,
    now: datetime | str | None = None,
    trust_score: float = 1.0,
) -> float:
    return score_candidate(
        candidate,
        weights=config.weights,
        now=now,
        reconfirmation_target=int(getattr(config, "reconfirmation_target", 2)),
        retrieval_hit_target=int(getattr(config, "retrieval_hit_target", 3)),
        survival_halflife_days=float(getattr(config, "survival_halflife_days", 7.0)),
        trust_score=trust_score,
    )
