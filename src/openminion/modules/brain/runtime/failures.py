"""Aggregate typed failure facts into structural recurrence buckets."""

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SeamId = Literal[
    "search_provider",
    "controlplane_route",
    "gateway_memory",
    "github_policy",
    "approval_decision",
    "adaptive_termination",
    "strategy_outcome",
    "low_progress",
]


class TypedFailureFact(BaseModel):
    """One typed failure fact projected from a seam emission."""

    model_config = ConfigDict(extra="forbid")

    seam_id: SeamId
    reason_code: str
    context_kind: str = ""
    recorded_at: str = ""
    trace_id: str = ""
    session_id: str = ""


class FailurePatternBucket(BaseModel):
    """Per-`(seam_id, reason_code)` recurrence bucket."""

    model_config = ConfigDict(extra="forbid")

    seam_id: SeamId
    reason_code: str
    recurrence_count: int = Field(default=0, ge=0)
    distinct_sessions: int = Field(default=0, ge=0)
    distinct_traces: int = Field(default=0, ge=0)
    earliest_recorded_at: str = ""
    latest_recorded_at: str = ""


class FailurePatternReadout(BaseModel):
    """Operator-facing readout of cross-session failure-pattern aggregates."""

    model_config = ConfigDict(extra="forbid")

    rows: list[FailurePatternBucket] = Field(default_factory=list)
    total_facts_scanned: int = Field(default=0, ge=0)
    distinct_seam_reason_pairs: int = Field(default=0, ge=0)
    evidence_window: dict[str, Any] = Field(default_factory=dict)


_VALID_SEAM_IDS: frozenset[str] = frozenset(
    {
        "search_provider",
        "controlplane_route",
        "gateway_memory",
        "github_policy",
        "approval_decision",
        "adaptive_termination",
        "strategy_outcome",
        "low_progress",
    }
)


def _emission_mapping(emission: Any) -> Mapping[str, Any] | None:
    if isinstance(emission, Mapping):
        return emission
    for attr in ("details", "content"):
        value = getattr(emission, attr, None)
        if isinstance(value, Mapping):
            return value
    synthesized: dict[str, Any] = {}
    for attr in (
        "reason_code",
        "context_kind",
        "recorded_at",
        "trace_id",
        "session_id",
        "outcome_status",
    ):
        value = getattr(emission, attr, None)
        if value is not None:
            synthesized[attr] = value
    return synthesized if synthesized else None


def project_seam_emissions_to_facts(
    emissions: Iterable[Any],
    *,
    seam_id: SeamId,
) -> list[TypedFailureFact]:
    """Project seam emissions into typed failure facts."""
    if seam_id not in _VALID_SEAM_IDS:
        raise KeyError(f"unknown seam_id: {seam_id!r}")

    facts: list[TypedFailureFact] = []
    for emission in emissions or []:
        mapping = _emission_mapping(emission)
        if mapping is None:
            continue
        reason_code = str(mapping.get("reason_code") or "").strip()
        if not reason_code and seam_id == "strategy_outcome":
            status = str(mapping.get("outcome_status") or "").strip().lower()
            if status == "failure":
                reason_code = "strategy_outcome_failure"
        if not reason_code:
            reason_code = f"{seam_id}_unknown"
        facts.append(
            TypedFailureFact(
                seam_id=seam_id,
                reason_code=reason_code,
                context_kind=str(mapping.get("context_kind") or "").strip(),
                recorded_at=str(mapping.get("recorded_at") or "").strip(),
                trace_id=str(mapping.get("trace_id") or "").strip(),
                session_id=str(mapping.get("session_id") or "").strip(),
            )
        )
    return facts


def aggregate_failure_patterns(
    facts: Iterable[TypedFailureFact],
    *,
    evidence_window: Mapping[str, Any] | None = None,
) -> FailurePatternReadout:
    """Aggregate typed failure facts into recurrence buckets."""
    materialized = list(facts)
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for fact in materialized:
        key = (fact.seam_id, fact.reason_code)
        agg = by_key.setdefault(
            key,
            {
                "recurrence_count": 0,
                "sessions": set(),
                "traces": set(),
                "timestamps": [],
            },
        )
        agg["recurrence_count"] += 1
        if fact.session_id:
            agg["sessions"].add(fact.session_id)
        if fact.trace_id:
            agg["traces"].add(fact.trace_id)
        if fact.recorded_at:
            agg["timestamps"].append(fact.recorded_at)

    rows: list[FailurePatternBucket] = []
    for (seam_id, reason_code), agg in by_key.items():
        timestamps = sorted(agg["timestamps"])
        rows.append(
            FailurePatternBucket(
                seam_id=seam_id,  # type: ignore[arg-type]
                reason_code=reason_code,
                recurrence_count=int(agg["recurrence_count"]),
                distinct_sessions=len(agg["sessions"]),
                distinct_traces=len(agg["traces"]),
                earliest_recorded_at=timestamps[0] if timestamps else "",
                latest_recorded_at=timestamps[-1] if timestamps else "",
            )
        )
    rows.sort(key=lambda row: (-row.recurrence_count, row.seam_id, row.reason_code))

    return FailurePatternReadout(
        rows=rows,
        total_facts_scanned=len(materialized),
        distinct_seam_reason_pairs=len(by_key),
        evidence_window=dict(evidence_window or {}),
    )


__all__ = [
    "SeamId",
    "TypedFailureFact",
    "FailurePatternBucket",
    "FailurePatternReadout",
    "project_seam_emissions_to_facts",
    "aggregate_failure_patterns",
]
