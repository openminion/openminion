from dataclasses import dataclass, replace
from datetime import datetime
import math
from typing import Any, Mapping, Sequence
import warnings

from ..diagnostics.operability import parse_iso_utc

from openminion.base.time import utc_now as _utc_now
from ..errors import InvalidArgumentError


TYPE_MULTIPLIERS: dict[str, tuple[str, float]] = {
    "correction": ("type_boost_correction", 1.5),
    "user_preference": ("type_boost_user_preference", 1.3),
    "pin": ("type_boost_pin", 1.2),
    "project_convention": ("type_boost_project_convention", 1.1),
    "meta_insight": ("type_boost_meta_insight", 1.05),
}


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def normalize_relevance(value: Any) -> float:
    """Normalize either a [0,1] score or a raw SQLite bm25 score."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if 0.0 <= numeric <= 1.0:
        return clamp01(numeric)
    return clamp01(1.0 / (1.0 + abs(numeric)))


def recency_score(age_days: float, half_life_days: float) -> float:
    if half_life_days <= 0:
        return 1.0
    return clamp01(0.5 ** (float(age_days) / float(half_life_days)))


@dataclass(frozen=True)
class RankingWeights:
    relevance: float = 0.45
    recency: float = 0.12
    feedback: float = 0.08
    type_bonus: float = 0.13
    confidence: float = 0.07
    outcome_utility: float = 0.15

    def __post_init__(self) -> None:
        values = {
            "relevance": float(self.relevance),
            "recency": float(self.recency),
            "feedback": float(self.feedback),
            "type_bonus": float(self.type_bonus),
            "confidence": float(self.confidence),
            "outcome_utility": float(self.outcome_utility),
        }
        if any(value < 0.0 for value in values.values()):
            raise InvalidArgumentError("ranking weights must be non-negative")
        total = sum(values.values())
        if total <= 0.0:
            raise InvalidArgumentError("ranking weights must sum to a positive value")
        if not math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-9):
            warnings.warn(
                "Ranking weights do not sum to 1.0; auto-normalizing.",
                UserWarning,
                stacklevel=2,
            )
            normalized = {name: value / total for name, value in values.items()}
            for name, value in normalized.items():
                object.__setattr__(self, name, value)


@dataclass(frozen=True)
class SignalVector:
    relevance: float
    recency: float
    feedback: float
    type_bonus: float
    confidence: float
    outcome_utility: float

    def as_dict(self) -> dict[str, float]:
        return {
            "relevance": self.relevance,
            "recency": self.recency,
            "feedback": self.feedback,
            "type_bonus": self.type_bonus,
            "confidence": self.confidence,
            "outcome_utility": self.outcome_utility,
        }


def _record_meta(record: Any) -> dict[str, Any]:
    if isinstance(record, Mapping):
        meta = record.get("meta", {})
    else:
        meta = getattr(record, "meta", {})
    if isinstance(meta, Mapping):
        return dict(meta)
    return {}


def _record_value(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(key, default)
    return getattr(record, key, default)


def _record_type(record: Any) -> str:
    return str(_record_value(record, "type", "") or "").strip().lower()


def _record_created_at(record: Any) -> str:
    created = _record_value(record, "created_at", None)
    if created:
        return str(created)
    meta = _record_meta(record)
    return str(meta.get("created_at", "") or "")


def _record_event_time(record: Any) -> str:
    event_time = _record_value(record, "event_time", None)
    if event_time:
        return str(event_time)
    return _record_created_at(record)


def _record_valid_to(record: Any) -> str | None:
    valid_to = _record_value(record, "valid_to", None)
    if not valid_to:
        return None
    return str(valid_to)


def _record_confidence(record: Any) -> float:
    raw = _record_value(record, "confidence", None)
    if raw is None:
        raw = _record_value(record, "trust_score", 0.6)
    try:
        return clamp01(float(raw))
    except (TypeError, ValueError):
        return 0.6


def _temporal_alignment(
    record: Any, *, temporal_anchor: datetime | None
) -> float | None:
    if temporal_anchor is None:
        return None
    event_time = parse_iso_utc(_record_event_time(record))
    if event_time is None:
        return None
    valid_to = parse_iso_utc(_record_valid_to(record))
    if temporal_anchor < event_time:
        return 0.0
    if valid_to is not None and temporal_anchor >= valid_to:
        return 0.0
    return 1.0


def _feedback_signal(record: Any, *, hit_divisor: float, feedback_max: float) -> float:
    meta = _record_meta(record)
    try:
        hit_count = max(0, int(meta.get("hit_count", 0) or 0))
    except (TypeError, ValueError):
        hit_count = 0
    divisor = max(1.0, float(hit_divisor))
    cap = max(0.0, float(feedback_max))
    return clamp01(min(hit_count / divisor, cap))


def outcome_utility_signal_from_meta(meta: Mapping[str, Any] | None) -> float:
    if not isinstance(meta, Mapping):
        return 0.5
    try:
        success_count = max(0, int(meta.get("outcome_success_count", 0) or 0))
    except (TypeError, ValueError):
        success_count = 0
    try:
        failure_count = max(0, int(meta.get("outcome_failure_count", 0) or 0))
    except (TypeError, ValueError):
        failure_count = 0
    observation_count = success_count + failure_count
    if observation_count <= 0:
        return 0.5
    try:
        feedback_score = clamp01(float(meta.get("feedback_score", 0.0) or 0.0))
    except (TypeError, ValueError):
        feedback_score = 0.0
    outcome_ratio = success_count / max(float(observation_count), 1.0)
    base_utility = clamp01((outcome_ratio + feedback_score) / 2.0)
    confidence = observation_count / (observation_count + 1.0)
    neutral = 0.5
    return clamp01(neutral + ((base_utility - neutral) * confidence))


def _type_multiplier(record_type: str, ranking_config: Any | None) -> float:
    attr_name, default = TYPE_MULTIPLIERS.get(record_type, ("", 1.0))
    if not attr_name or ranking_config is None:
        return default
    return float(getattr(ranking_config, attr_name, default))


def _type_multiplier_values(ranking_config: Any | None) -> list[float]:
    return [
        float(getattr(ranking_config, attr_name, default))
        if ranking_config is not None
        else default
        for attr_name, default in TYPE_MULTIPLIERS.values()
    ]


def extract_signals(
    record: Any,
    *,
    ranking_config: Any | None = None,
    query_bm25_score: float | None = None,
    now: datetime | None = None,
    temporal_anchor: datetime | None = None,
) -> SignalVector:
    active_now = now or _utc_now()
    meta = _record_meta(record)
    relevance_raw = (
        query_bm25_score
        if query_bm25_score is not None
        else meta.get("bm25_score", _record_value(record, "bm25_score", 0.0))
    )
    relevance = normalize_relevance(relevance_raw)
    temporal_alignment = _temporal_alignment(record, temporal_anchor=temporal_anchor)
    if temporal_alignment is not None:
        relevance = clamp01((relevance + temporal_alignment) / 2.0)

    created_at = parse_iso_utc(_record_created_at(record))
    age_days = 0.0
    if created_at is not None:
        age_days = max(
            0.0,
            (active_now - created_at).total_seconds() / 86400.0,
        )
    half_life_days = float(getattr(ranking_config, "recency_half_life_days", 30.0))
    recency = recency_score(age_days, half_life_days)

    feedback = _feedback_signal(
        record,
        hit_divisor=float(getattr(ranking_config, "feedback_hit_divisor", 10.0)),
        feedback_max=float(getattr(ranking_config, "feedback_max", 1.0)),
    )
    outcome_utility = outcome_utility_signal_from_meta(meta)

    max_multiplier = max([1.0, *_type_multiplier_values(ranking_config)])
    multiplier = _type_multiplier(_record_type(record), ranking_config)
    if max_multiplier <= 1.0:
        type_bonus = 0.0
    else:
        type_bonus = clamp01((multiplier - 1.0) / (max_multiplier - 1.0))

    confidence = _record_confidence(record)
    return SignalVector(
        relevance=relevance,
        recency=recency,
        feedback=feedback,
        type_bonus=type_bonus,
        confidence=confidence,
        outcome_utility=outcome_utility,
    )


def compute_unified_score(signals: SignalVector, weights: RankingWeights) -> float:
    return clamp01(
        (weights.relevance * signals.relevance)
        + (weights.recency * signals.recency)
        + (weights.feedback * signals.feedback)
        + (weights.type_bonus * signals.type_bonus)
        + (weights.confidence * signals.confidence)
        + (weights.outcome_utility * signals.outcome_utility)
    )


def _breakdown(signals: SignalVector, unified_score: float) -> dict[str, float]:
    payload = signals.as_dict()
    payload["unified_score"] = clamp01(unified_score)
    return payload


def _score_weights(ranking_config: Any | None) -> RankingWeights:
    if ranking_config is None:
        return RankingWeights()
    return RankingWeights(
        relevance=float(getattr(ranking_config, "w_relevance", 0.45)),
        recency=float(getattr(ranking_config, "w_recency", 0.12)),
        feedback=float(getattr(ranking_config, "w_feedback", 0.08)),
        type_bonus=float(getattr(ranking_config, "w_type_bonus", 0.13)),
        confidence=float(getattr(ranking_config, "w_confidence", 0.07)),
        outcome_utility=float(getattr(ranking_config, "w_outcome_utility", 0.15)),
    )


def _apply_breakdown(record: Any, *, score: float, breakdown: dict[str, float]) -> Any:
    if isinstance(record, Mapping):
        scored = dict(record)
        meta = _record_meta(record)
        meta["score_breakdown"] = dict(breakdown)
        scored["meta"] = meta
        scored["score"] = clamp01(score)
        scored["unified_score"] = clamp01(score)
        return scored
    meta = _record_meta(record)
    meta["score_breakdown"] = dict(breakdown)
    meta["unified_score"] = clamp01(score)
    return replace(record, meta=meta)


def score_record(
    record: Any,
    *,
    ranking_config: Any | None = None,
    query_bm25_score: float | None = None,
    now: datetime | None = None,
    temporal_anchor: datetime | None = None,
) -> Any:
    signals = extract_signals(
        record,
        ranking_config=ranking_config,
        query_bm25_score=query_bm25_score,
        now=now,
        temporal_anchor=temporal_anchor,
    )
    weights = _score_weights(ranking_config)
    unified_score = compute_unified_score(signals, weights)
    return _apply_breakdown(
        record,
        score=unified_score,
        breakdown=_breakdown(signals, unified_score),
    )


def score_records(
    records: Sequence[Any],
    *,
    ranking_config: Any | None = None,
    query_bm25_scores: Sequence[float | None] | None = None,
    now: datetime | None = None,
    temporal_anchor: datetime | None = None,
) -> list[Any]:
    def _record_unified_score(item: Any) -> float:
        if isinstance(item, Mapping):
            return clamp01(
                float(
                    _record_value(
                        item,
                        "unified_score",
                        _record_value(item, "score", 0.0),
                    )
                    or 0.0
                )
            )
        meta = _record_meta(item)
        breakdown = meta.get("score_breakdown", {})
        if isinstance(breakdown, Mapping):
            try:
                return clamp01(float(breakdown.get("unified_score", 0.0) or 0.0))
            except (TypeError, ValueError):
                return 0.0
        return clamp01(float(meta.get("unified_score", 0.0) or 0.0))

    scored: list[Any] = []
    for index, record in enumerate(records):
        query_score = None
        if query_bm25_scores is not None and index < len(query_bm25_scores):
            query_score = query_bm25_scores[index]
        scored.append(
            score_record(
                record,
                ranking_config=ranking_config,
                query_bm25_score=query_score,
                now=now,
                temporal_anchor=temporal_anchor,
            )
        )
    scored.sort(
        key=lambda item: (
            round(_record_unified_score(item), 4),
            str(_record_value(item, "created_at", "") or ""),
        ),
        reverse=True,
    )
    return scored
