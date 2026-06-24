from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from ...constants import (
    RVRH_DEFAULT_CONFIDENCE_THRESHOLD,
    RVRH_DEFAULT_FRESHNESS_CAP_SECONDS,
)


RecallSource = Literal["memory", "context", "recompute"]
DEFAULT_CONFIDENCE_THRESHOLD: float = RVRH_DEFAULT_CONFIDENCE_THRESHOLD
DEFAULT_FRESHNESS_CAP_SECONDS: int | None = RVRH_DEFAULT_FRESHNESS_CAP_SECONDS


@dataclass(frozen=True)
class RecallDecision:
    """Typed recall decision."""

    source: RecallSource
    confidence_threshold: float
    freshness_cap_seconds: int | None
    reason: str
    observed_confidence: float = 0.0
    observed_age_seconds: int | None = None
    record_id: str = ""


RECALL_REASON_USE_MEMORY = "use_memory_confident_fresh"
RECALL_REASON_FALLBACK_CONTEXT = "no_record_fallback_context"
RECALL_REASON_RECOMPUTE_LOW_CONFIDENCE = "recompute_low_confidence"
RECALL_REASON_RECOMPUTE_STALE = "recompute_stale_record"
RECALL_REASON_RECOMPUTE_INVALIDATED = "recompute_record_invalidated"


def _parse_iso_to_aware(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _record_confidence(record: Any) -> float:
    try:
        return float(getattr(record, "confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _read_record_id(record: Any) -> str:
    return str(getattr(record, "id", "") or "")


def _record_age_seconds(record: Any, *, now: datetime) -> int | None:
    """Return the age of the record's ``valid_to`` in seconds, if any."""

    valid_to_raw = getattr(record, "valid_to", None)
    if not valid_to_raw:
        return None
    parsed = _parse_iso_to_aware(str(valid_to_raw))
    if parsed is None:
        return None
    delta = now - parsed
    return max(0, int(delta.total_seconds()))


def _record_is_invalidated(record: Any, *, now: datetime) -> bool:
    """Return whether the record is invalidated at ``now``."""

    valid_to_raw = getattr(record, "valid_to", None)
    if not valid_to_raw:
        return False
    parsed = _parse_iso_to_aware(str(valid_to_raw))
    if parsed is None:
        return False
    return parsed <= now


def _decision(
    *,
    source: RecallSource,
    threshold: float,
    cap: int | None,
    reason: str,
    confidence: float,
    age_seconds: int | None,
    record_id: str,
) -> RecallDecision:
    return RecallDecision(
        source=source,
        confidence_threshold=threshold,
        freshness_cap_seconds=cap,
        reason=reason,
        observed_confidence=confidence,
        observed_age_seconds=age_seconds,
        record_id=record_id,
    )


def resolve_recall_decision(
    record: Any | None,
    *,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    freshness_cap_seconds: int | None = DEFAULT_FRESHNESS_CAP_SECONDS,
    now: datetime | None = None,
) -> RecallDecision:
    """Resolve the recall source for one candidate record."""

    effective_now = now if now is not None else datetime.now(timezone.utc)
    threshold = float(confidence_threshold)
    cap = (
        freshness_cap_seconds
        if freshness_cap_seconds is None
        else int(freshness_cap_seconds)
    )

    if record is None:
        return _decision(
            source="context",
            threshold=threshold,
            cap=cap,
            reason=RECALL_REASON_FALLBACK_CONTEXT,
            confidence=0.0,
            age_seconds=None,
            record_id="",
        )

    record_id = _read_record_id(record)
    confidence = _record_confidence(record)
    age_seconds = _record_age_seconds(record, now=effective_now)
    invalidated = _record_is_invalidated(record, now=effective_now)

    if invalidated:
        return _decision(
            source="recompute",
            threshold=threshold,
            cap=cap,
            reason=RECALL_REASON_RECOMPUTE_INVALIDATED,
            confidence=confidence,
            age_seconds=age_seconds,
            record_id=record_id,
        )

    if cap is not None and age_seconds is not None and age_seconds > cap:
        return _decision(
            source="recompute",
            threshold=threshold,
            cap=cap,
            reason=RECALL_REASON_RECOMPUTE_STALE,
            confidence=confidence,
            age_seconds=age_seconds,
            record_id=record_id,
        )

    if confidence < threshold:
        return _decision(
            source="recompute",
            threshold=threshold,
            cap=cap,
            reason=RECALL_REASON_RECOMPUTE_LOW_CONFIDENCE,
            confidence=confidence,
            age_seconds=age_seconds,
            record_id=record_id,
        )

    return _decision(
        source="memory",
        threshold=threshold,
        cap=cap,
        reason=RECALL_REASON_USE_MEMORY,
        confidence=confidence,
        age_seconds=age_seconds,
        record_id=record_id,
    )


def decision_telemetry_payload(decision: RecallDecision) -> dict[str, Any]:
    """Render a small structural telemetry payload for a decision."""

    return {
        "source": decision.source,
        "reason": decision.reason,
        "confidence_threshold": decision.confidence_threshold,
        "freshness_cap_seconds": decision.freshness_cap_seconds,
        "observed_confidence": decision.observed_confidence,
        "observed_age_seconds": decision.observed_age_seconds,
        "record_id": decision.record_id,
    }


__all__ = [
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "DEFAULT_FRESHNESS_CAP_SECONDS",
    "RECALL_REASON_FALLBACK_CONTEXT",
    "RECALL_REASON_RECOMPUTE_INVALIDATED",
    "RECALL_REASON_RECOMPUTE_LOW_CONFIDENCE",
    "RECALL_REASON_RECOMPUTE_STALE",
    "RECALL_REASON_USE_MEMORY",
    "RecallDecision",
    "RecallSource",
    "decision_telemetry_payload",
    "resolve_recall_decision",
]
