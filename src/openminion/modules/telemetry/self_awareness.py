"""Telemetry payload helpers for runtime self-awareness surfaces."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from openminion.modules.runtime.self_model import (
    SELF_MODEL_HEALTH_OK,
    SelfModelSnapshot,
)
from openminion.modules.telemetry.events.catalog import (
    IMPROVEMENT_CANDIDATE_PROMOTED,
    IMPROVEMENT_CANDIDATE_ROLLED_BACK,
    IMPROVEMENT_CANDIDATE_STAGED,
    IMPROVEMENT_CANDIDATE_SUPPRESSED,
    SELF_AWARENESS_ANSWER_DEGRADED,
    SELF_MODEL_SNAPSHOT_BUILT,
    SELF_MODEL_SNAPSHOT_DEGRADED,
)

_CANDIDATE_EVENT_TYPES = frozenset(
    {
        IMPROVEMENT_CANDIDATE_STAGED,
        IMPROVEMENT_CANDIDATE_PROMOTED,
        IMPROVEMENT_CANDIDATE_ROLLED_BACK,
        IMPROVEMENT_CANDIDATE_SUPPRESSED,
    }
)


def build_self_model_snapshot_event(
    snapshot: SelfModelSnapshot | Mapping[str, Any],
    *,
    source: str = "runtime",
) -> tuple[str, dict[str, Any]]:
    """Build telemetry for a snapshot compose attempt."""

    snapshot_obj = (
        snapshot
        if isinstance(snapshot, SelfModelSnapshot)
        else SelfModelSnapshot.model_validate(snapshot)
    )
    event_type = (
        SELF_MODEL_SNAPSHOT_BUILT
        if snapshot_obj.health == SELF_MODEL_HEALTH_OK
        else SELF_MODEL_SNAPSHOT_DEGRADED
    )
    sections = {
        "identity": snapshot_obj.identity.status,
        "capabilities": snapshot_obj.capabilities.status,
        "policy": snapshot_obj.policy.status,
        "memory_state": snapshot_obj.memory_state.status,
        "context_state": snapshot_obj.context_state.status,
        "knowledge_state": snapshot_obj.knowledge_state.status,
        "improvement_state": snapshot_obj.improvement_state.status,
    }
    return event_type, {
        "schema_version": snapshot_obj.schema_version,
        "source": str(source or "runtime"),
        "agent_id": snapshot_obj.agent_id,
        "health": snapshot_obj.health,
        "degraded_reasons": list(snapshot_obj.degraded_reasons),
        "sections": sections,
    }


def build_self_awareness_answer_degraded_event(
    *,
    agent_id: str,
    question_kind: str,
    degraded_reasons: list[str],
) -> tuple[str, dict[str, Any]]:
    """Build telemetry for deterministic degraded self-awareness answers."""

    return SELF_AWARENESS_ANSWER_DEGRADED, {
        "agent_id": str(agent_id or "unknown"),
        "question_kind": str(question_kind or "unknown"),
        "degraded_reasons": [str(item) for item in degraded_reasons],
    }


def build_improvement_candidate_event(
    event_type: str,
    candidate: Mapping[str, Any] | Any,
) -> tuple[str, dict[str, Any]]:
    """Build telemetry for the generic candidate lifecycle."""

    if event_type not in _CANDIDATE_EVENT_TYPES:
        raise ValueError(f"unsupported improvement candidate event: {event_type}")
    evidence_refs = _field(candidate, "evidence_refs", [])
    return event_type, {
        "candidate_id": str(_field(candidate, "candidate_id", "")),
        "target_type": str(_field(candidate, "target_type", "")),
        "target_owner": str(_field(candidate, "target_owner", "")),
        "state": str(_field(candidate, "state", "")),
        "risk_level": str(_field(candidate, "risk_level", "")),
        "review_mode": str(_field(candidate, "review_mode", "")),
        "evidence_ref_count": len(evidence_refs)
        if isinstance(evidence_refs, list)
        else 0,
    }


def _field(source: Mapping[str, Any] | Any, name: str, default: Any) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


__all__ = [
    "build_improvement_candidate_event",
    "build_self_awareness_answer_degraded_event",
    "build_self_model_snapshot_event",
]
