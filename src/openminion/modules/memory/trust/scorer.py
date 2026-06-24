"""Composite trust scoring for memory promotion."""

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from openminion.modules.memory.diagnostics.operability import parse_iso_utc
from openminion.modules.memory.errors import InvalidArgumentError
from openminion.modules.memory.models import MemoryCandidate
from openminion.modules.memory.runtime.scorer import clamp01, recency_score

from .constants import (
    DEFAULT_OPPOSING_PEER_HALF_LIFE_DAYS,
    DEFAULT_SOURCE_PROVENANCE,
    DURABLE_RECORD_PAGE_LIMIT,
)
from .rate_limit import RateLimitDecision
from .types import MemorySourceClass, TrustScore

TrustScoreReasonCode = Literal["ALLOWED", "MISSING_CLAIM_KEY"]


@dataclass(frozen=True)
class TrustScoreResult:
    trust_score: TrustScore
    reason_code: TrustScoreReasonCode
    peer_count: int
    source_class: MemorySourceClass | None


def _record_value(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(key, default)
    return getattr(record, key, default)


def _record_meta(record: Any) -> dict[str, Any]:
    raw = _record_value(record, "meta", {})
    if isinstance(raw, Mapping):
        return dict(raw)
    return {}


def _record_text(record: Any, *keys: str, meta_key: str | None = None) -> str | None:
    for key in keys:
        raw = _record_value(record, key, None)
        if raw is not None:
            break
    else:
        raw = _record_meta(record).get(meta_key or keys[-1])
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _record_claim_key(record: Any) -> str | None:
    return _record_text(record, "claim_key")


def _record_polarity(record: Any) -> str:
    text = str(_record_text(record, "polarity") or "asserts").lower()
    return text if text in {"asserts", "negates"} else "asserts"


def _record_source_class(record: Any) -> MemorySourceClass | None:
    text = _record_text(record, "source_class") or ""
    if text in DEFAULT_SOURCE_PROVENANCE:
        return text  # type: ignore[return-value]
    return None


def _record_valid_to(record: Any) -> str | None:
    return _record_text(record, "valid_to")


def _record_event_time(record: Any) -> str | None:
    return _record_text(record, "event_time", "created_at", meta_key="event_time")


def _record_confidence(record: Any) -> float:
    raw = _record_value(record, "confidence", None)
    if raw is None:
        raw = _record_meta(record).get("confidence")
    try:
        return clamp01(float(raw))
    except (TypeError, ValueError):
        return 0.0


def _iter_durable_records(repo: Any) -> Iterator[Any]:
    if isinstance(repo, Iterable) and not hasattr(repo, "list"):
        yield from repo
        return
    if hasattr(repo, "_iter_all_records_for_forget"):
        yield from repo._iter_all_records_for_forget()  # noqa: SLF001
        return
    if hasattr(repo, "iter_all_records"):
        yield from repo.iter_all_records()
        return
    if hasattr(repo, "list_all"):
        yield from repo.list_all()
        return
    if hasattr(repo, "list_scopes") and hasattr(repo, "list"):
        from openminion.modules.memory.storage.base import ListQueryOptions, RecordOrder

        for scope in list(repo.list_scopes()):
            offset = 0
            while True:
                page = repo.list(
                    ListQueryOptions(
                        scopes=[scope],
                        limit=DURABLE_RECORD_PAGE_LIMIT,
                        offset=offset,
                        order_by=RecordOrder.UPDATED_AT_ASC,
                    )
                )
                if not page:
                    break
                yield from page
                if len(page) < DURABLE_RECORD_PAGE_LIMIT:
                    break
                offset += DURABLE_RECORD_PAGE_LIMIT
        return
    raise InvalidArgumentError(
        "repo must be iterable or expose an all-records listing surface"
    )


def _count_corroborating_peers(candidate: MemoryCandidate, repo: Any) -> int:
    claim_key = _record_claim_key(candidate)
    if claim_key is None:
        return 0
    polarity = _record_polarity(candidate)
    count = 0
    for record in _iter_durable_records(repo):
        if bool(_record_value(record, "is_deleted", False)):
            continue
        if _record_claim_key(record) != claim_key:
            continue
        if _record_polarity(record) != polarity:
            continue
        count += 1
    return count


def _current_opposing_peer_penalty(candidate: MemoryCandidate, repo: Any) -> float:
    claim_key = _record_claim_key(candidate)
    if claim_key is None:
        return 0.0
    polarity = _record_polarity(candidate)
    opposite_polarity = "negates" if polarity == "asserts" else "asserts"
    now = datetime.now(timezone.utc)
    penalty = 0.0
    for record in _iter_durable_records(repo):
        if bool(_record_value(record, "is_deleted", False)):
            continue
        if _record_claim_key(record) != claim_key:
            continue
        if _record_polarity(record) != opposite_polarity:
            continue
        valid_to = parse_iso_utc(_record_valid_to(record))
        if valid_to is not None and valid_to <= now:
            continue
        event_time = parse_iso_utc(_record_event_time(record))
        age_days = 0.0
        if event_time is not None:
            age_days = max(0.0, (now - event_time).total_seconds() / 86400.0)
        contribution = _record_confidence(record) * recency_score(
            age_days=age_days,
            half_life_days=DEFAULT_OPPOSING_PEER_HALF_LIFE_DAYS,
        )
        penalty += contribution
    return clamp01(penalty)


def _source_provenance(
    candidate: MemoryCandidate,
    *,
    provenance_table: Mapping[MemorySourceClass, float],
) -> tuple[MemorySourceClass | None, float]:
    source_class = _record_source_class(candidate)
    if source_class is None:
        return None, 0.0
    return source_class, clamp01(float(provenance_table.get(source_class, 0.0)))


def compute_trust_score(
    candidate: MemoryCandidate,
    repo: Any,
    source_window: RateLimitDecision,
    *,
    corroboration_target: int = 2,
    provenance_table: Mapping[MemorySourceClass, float] | None = None,
) -> TrustScoreResult:
    active_table = provenance_table or DEFAULT_SOURCE_PROVENANCE
    source_class, source_provenance = _source_provenance(
        candidate,
        provenance_table=active_table,
    )
    rate_limit_pressure = (
        source_window.observed_promotions / source_window.max_promotions
        if source_window.max_promotions
        else 0.0
    )
    claim_key = _record_claim_key(candidate)
    if claim_key is None:
        trust_score = TrustScore(
            source_provenance=source_provenance,
            corroboration=0.0,
            contradiction_penalty=0.0,
            rate_limit_pressure=rate_limit_pressure,
            score=0.0,
        )
        return TrustScoreResult(
            trust_score=trust_score,
            reason_code="MISSING_CLAIM_KEY",
            peer_count=0,
            source_class=source_class,
        )
    peer_count = _count_corroborating_peers(candidate, repo)
    corroboration = clamp01(peer_count / max(float(corroboration_target), 1.0))
    contradiction_penalty = _current_opposing_peer_penalty(candidate, repo)
    score = clamp01(
        (source_provenance * (1.0 + corroboration))
        * (1.0 - rate_limit_pressure)
        * (1.0 - contradiction_penalty)
    )
    trust_score = TrustScore(
        source_provenance=source_provenance,
        corroboration=corroboration,
        contradiction_penalty=contradiction_penalty,
        rate_limit_pressure=rate_limit_pressure,
        score=score,
    )
    return TrustScoreResult(
        trust_score=trust_score,
        reason_code="ALLOWED",
        peer_count=peer_count,
        source_class=source_class,
    )
