from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from .decision import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_FRESHNESS_CAP_SECONDS,
    RecallDecision,
    decision_telemetry_payload,
    resolve_recall_decision,
)
from openminion.modules.telemetry.events.catalog import RVRH_RECALL_DECISION


def consult_recall_decisions(
    records: Sequence[Any] | None,
    *,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    freshness_cap_seconds: int | None = DEFAULT_FRESHNESS_CAP_SECONDS,
    now: datetime | None = None,
) -> list[RecallDecision]:
    """Resolve one recall decision per record."""

    effective_now = now if now is not None else datetime.now(timezone.utc)
    record_list = list(records or [])
    if not record_list:
        return [
            resolve_recall_decision(
                None,
                confidence_threshold=confidence_threshold,
                freshness_cap_seconds=freshness_cap_seconds,
                now=effective_now,
            )
        ]
    return [
        resolve_recall_decision(
            record,
            confidence_threshold=confidence_threshold,
            freshness_cap_seconds=freshness_cap_seconds,
            now=effective_now,
        )
        for record in record_list
    ]


def stamp_recall_decision(
    decisions: Iterable[RecallDecision],
    *,
    logger: Any,
) -> None:
    """Stamp one telemetry event per recall decision."""

    if logger is None:
        return
    for decision in decisions:
        try:
            logger.log_canonical_event(
                event_type=RVRH_RECALL_DECISION,
                payload=decision_telemetry_payload(decision),
            )
        except Exception:
            continue


def summarize_decisions(decisions: Sequence[RecallDecision]) -> dict[str, int]:
    """Return a small histogram of decision sources."""

    histogram = {"memory": 0, "context": 0, "recompute": 0}
    for decision in decisions:
        if decision.source in histogram:
            histogram[decision.source] += 1
    return histogram


__all__ = [
    "consult_recall_decisions",
    "stamp_recall_decision",
    "summarize_decisions",
]
