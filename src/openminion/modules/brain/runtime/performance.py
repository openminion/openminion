from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SubjectKind = Literal["strategy", "workflow"]

GroupByAxis = Literal[
    "strategy_id",
    "strategy_id_capability",
    "strategy_id_intent",
    "strategy_id_capability_intent",
    "workflow_id",
]

_SUBJECT_KIND_BY_AXIS: dict[GroupByAxis, SubjectKind] = {
    "strategy_id": "strategy",
    "strategy_id_capability": "strategy",
    "strategy_id_intent": "strategy",
    "strategy_id_capability_intent": "strategy",
    "workflow_id": "workflow",
}

_SUBJECT_ID_FIELDS: dict[GroupByAxis, tuple[str, ...]] = {
    "strategy_id": ("strategy_id",),
    "strategy_id_capability": ("strategy_id", "capability_category"),
    "strategy_id_intent": ("strategy_id", "intent_category"),
    "strategy_id_capability_intent": (
        "strategy_id",
        "capability_category",
        "intent_category",
    ),
    "workflow_id": ("workflow_id",),
}


class PerformanceRegistryEntry(BaseModel):
    """Per-subject aggregate of typed outcome observations."""

    model_config = ConfigDict(extra="forbid")

    subject_kind: SubjectKind
    subject_id: str
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    other_count: int = Field(default=0, ge=0)
    distinct_traces: int = Field(default=0, ge=0)
    earliest_observed_at: str = ""
    latest_observed_at: str = ""
    evidence_window: dict[str, Any] = Field(default_factory=dict)


class PerformanceRegistry(BaseModel):
    """Operator-facing typed performance registry."""

    model_config = ConfigDict(extra="forbid")

    entries: list[PerformanceRegistryEntry] = Field(default_factory=list)
    total_records_scanned: int = Field(default=0, ge=0)
    registry_version: str = "1"


class RankingDecision(BaseModel):
    """One typed ranking decision over a candidate set."""

    model_config = ConfigDict(extra="forbid")

    subject_kind: SubjectKind
    subject_id: str
    supporting_entry_refs: list[str] = Field(default_factory=list)
    policy_id: str = ""
    produced_at: str = ""


def _record_content(record: Any) -> Mapping[str, Any] | None:
    if isinstance(record, Mapping):
        inner = record.get("content")
        return inner if isinstance(inner, Mapping) else record
    inner = getattr(record, "content", None)
    return inner if isinstance(inner, Mapping) else None


def _compose_subject_id(content: Mapping[str, Any], fields: tuple[str, ...]) -> str:
    parts: list[str] = []
    for field in fields:
        value = str(content.get(field) or "").strip()
        if not value:
            return ""
        parts.append(value)
    return "|".join(parts)


def _outcome_bucket(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "success":
        return "success"
    if normalized == "failure":
        return "failure"
    return "other"


def _observation_total(entry: PerformanceRegistryEntry) -> int:
    return entry.success_count + entry.failure_count + entry.other_count


def project_strategy_outcome_records_to_entries(
    records: Iterable[Any],
    *,
    group_by: GroupByAxis,
    evidence_window: Mapping[str, Any] | None = None,
) -> list[PerformanceRegistryEntry]:
    if group_by not in _SUBJECT_KIND_BY_AXIS:
        raise KeyError(f"unknown group_by axis: {group_by!r}")
    subject_kind = _SUBJECT_KIND_BY_AXIS[group_by]
    fields = _SUBJECT_ID_FIELDS[group_by]
    window = dict(evidence_window or {})

    by_subject: dict[str, dict[str, Any]] = {}
    for record in records or []:
        content = _record_content(record)
        if content is None:
            continue
        subject_id = _compose_subject_id(content, fields)
        if not subject_id:
            continue
        bucket = _outcome_bucket(str(content.get("outcome_status") or ""))
        trace_id = str(content.get("turn_id") or "").strip()
        recorded_at = str(content.get("created_at") or "").strip()

        agg = by_subject.setdefault(
            subject_id,
            {
                "success_count": 0,
                "failure_count": 0,
                "other_count": 0,
                "traces": set(),
                "timestamps": [],
            },
        )
        agg[f"{bucket}_count"] += 1
        if trace_id:
            agg["traces"].add(trace_id)
        if recorded_at:
            agg["timestamps"].append(recorded_at)

    entries: list[PerformanceRegistryEntry] = []
    for subject_id, agg in by_subject.items():
        timestamps = sorted(agg["timestamps"])
        entries.append(
            PerformanceRegistryEntry(
                subject_kind=subject_kind,
                subject_id=subject_id,
                success_count=int(agg["success_count"]),
                failure_count=int(agg["failure_count"]),
                other_count=int(agg["other_count"]),
                distinct_traces=len(agg["traces"]),
                earliest_observed_at=timestamps[0] if timestamps else "",
                latest_observed_at=timestamps[-1] if timestamps else "",
                evidence_window=dict(window),
            )
        )
    entries.sort(key=lambda entry: (-_observation_total(entry), entry.subject_id))
    return entries


def aggregate_performance_registry(
    entries: Iterable[PerformanceRegistryEntry],
    *,
    evidence_window: Mapping[str, Any] | None = None,
    registry_version: str = "1",
) -> PerformanceRegistry:
    materialized = list(entries)
    total = sum(_observation_total(entry) for entry in materialized)
    del evidence_window
    return PerformanceRegistry(
        entries=materialized,
        total_records_scanned=total,
        registry_version=str(registry_version or "1"),
    )


def rank_candidates(
    registry: PerformanceRegistry,
    *,
    candidate_ids: Iterable[str],
    policy_id: str,
    produced_at: str = "",
) -> list[RankingDecision]:
    by_id: dict[str, PerformanceRegistryEntry] = {
        entry.subject_id: entry for entry in registry.entries
    }
    candidate_list = [
        candidate_id
        for candidate_id in (str(cid or "").strip() for cid in (candidate_ids or []))
        if candidate_id
    ]
    policy_id_value = str(policy_id or "").strip()
    produced_at_value = str(produced_at or "").strip()
    decisions: list[RankingDecision] = []
    for candidate_id in candidate_list:
        entry = by_id.get(candidate_id)
        if entry is None:
            decisions.append(
                RankingDecision(
                    subject_kind="strategy",
                    subject_id=candidate_id,
                    supporting_entry_refs=[],
                    policy_id=policy_id_value,
                    produced_at=produced_at_value,
                )
            )
            continue
        decisions.append(
            RankingDecision(
                subject_kind=entry.subject_kind,
                subject_id=candidate_id,
                supporting_entry_refs=[entry.subject_id],
                policy_id=policy_id_value,
                produced_at=produced_at_value,
            )
        )
    decisions.sort(
        key=lambda d: (
            0 if d.supporting_entry_refs else 1,
            -(_observation_total(by_id[d.subject_id]) if d.subject_id in by_id else 0),
            d.subject_id,
        )
    )
    return decisions


__all__ = [
    "SubjectKind",
    "GroupByAxis",
    "PerformanceRegistryEntry",
    "PerformanceRegistry",
    "RankingDecision",
    "project_strategy_outcome_records_to_entries",
    "aggregate_performance_registry",
    "rank_candidates",
]
