"""Compose self-improvement aggregates into one operator readout."""

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.brain.runtime.failures import (
    FailurePatternReadout,
)
from openminion.modules.brain.runtime.attribution import AttributionReadout
from openminion.modules.brain.runtime.performance import PerformanceRegistry


class SelfImprovementReadout(BaseModel):
    """Operator-facing view across attribution, recurrence, and performance."""

    model_config = ConfigDict(extra="forbid")

    attribution: AttributionReadout = Field(default_factory=AttributionReadout)
    failure_patterns: FailurePatternReadout = Field(
        default_factory=FailurePatternReadout
    )
    performance: PerformanceRegistry = Field(default_factory=PerformanceRegistry)
    candidate_usefulness: list[dict[str, Any]] = Field(default_factory=list)
    evidence_window: dict[str, Any] = Field(default_factory=dict)


def compose_self_improvement_readout(
    *,
    attribution: AttributionReadout | Mapping[str, Any] | None = None,
    failure_patterns: FailurePatternReadout | Mapping[str, Any] | None = None,
    performance: PerformanceRegistry | Mapping[str, Any] | None = None,
    evidence_window: Mapping[str, Any] | None = None,
) -> SelfImprovementReadout:
    """Merge existing runtime aggregates without reinterpreting user prose."""

    attribution_obj = (
        attribution
        if isinstance(attribution, AttributionReadout)
        else AttributionReadout.model_validate(attribution or {})
    )
    failure_obj = (
        failure_patterns
        if isinstance(failure_patterns, FailurePatternReadout)
        else FailurePatternReadout.model_validate(failure_patterns or {})
    )
    performance_obj = (
        performance
        if isinstance(performance, PerformanceRegistry)
        else PerformanceRegistry.model_validate(performance or {})
    )
    return SelfImprovementReadout(
        attribution=attribution_obj,
        failure_patterns=failure_obj,
        performance=performance_obj,
        candidate_usefulness=_candidate_usefulness_rows(attribution_obj),
        evidence_window=dict(evidence_window or {}),
    )


def _candidate_usefulness_rows(readout: AttributionReadout) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in readout.rows:
        success = int(row.by_outcome_status.get("success", 0) or 0)
        failure = int(row.by_outcome_status.get("failure", 0) or 0)
        rows.append(
            {
                "candidate_ref": row.retrieved_record_id,
                "total_events": row.total_events,
                "success_count": success,
                "failure_count": failure,
                "net_success_count": success - failure,
                "distinct_traces": row.distinct_traces,
            }
        )
    rows.sort(
        key=lambda item: (
            -int(item["net_success_count"]),
            -int(item["total_events"]),
            str(item["candidate_ref"]),
        )
    )
    return rows


__all__ = [
    "SelfImprovementReadout",
    "compose_self_improvement_readout",
]
