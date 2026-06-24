from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


CanonicalLifecyclePhase = Literal[
    "pending",
    "active",
    "awaiting_user",
    "awaiting_async",
    "delegated_waiting",
    "paused",
    "completed",
    "cancelled",
    "failed",
    "unknown",
]


ResumeChannel = Literal[
    "direct",
    "cron",
    "persistent_service",
    "delegated",
    "none",
    "unknown",
]


SourceVocabulary = Literal[
    "task_status",
    "plan_step_status",
    "mission_status",
    "working_status",
    "synthetic",
]


_TASK_STATUS_TO_PHASE: dict[str, CanonicalLifecyclePhase] = {
    "PENDING": "pending",
    "ACTIVE": "active",
    "WAITING": "awaiting_async",
    "DONE": "completed",
    "CANCELED": "cancelled",
}

_PLAN_STEP_STATUS_TO_PHASE: dict[str, CanonicalLifecyclePhase] = {
    "PENDING": "pending",
    "ACTIVE": "active",
    "DONE": "completed",
    "FAILED": "failed",
    "BLOCKED": "awaiting_async",
}

_MISSION_STATUS_TO_PHASE: dict[str, CanonicalLifecyclePhase] = {
    "active": "active",
    "paused": "paused",
    "awaiting_async": "awaiting_async",
    "completed": "completed",
    "cancelled": "cancelled",
    "halted": "failed",
}

_WORKING_STATUS_TO_PHASE: dict[str, CanonicalLifecyclePhase] = {
    "active": "active",
    "continue": "active",
    "waiting_user": "awaiting_user",
    "job_pending": "awaiting_async",
    "done": "completed",
    "error": "failed",
    "stopped": "paused",
}


class LifecyclePhaseProjection(BaseModel):
    """Typed projection of one source-vocabulary value to canonical phase."""

    model_config = ConfigDict(extra="forbid")

    source_vocab: SourceVocabulary
    source_value: str
    phase: CanonicalLifecyclePhase


class ResumeTransition(BaseModel):
    """Typed resume-channel → canonical-phase transition fact."""

    model_config = ConfigDict(extra="forbid")

    channel: ResumeChannel
    from_phase: CanonicalLifecyclePhase
    to_phase: CanonicalLifecyclePhase


class UnifiedLifecycleProjection(BaseModel):
    """Operator-facing unified lifecycle view over the existing typed owners."""

    model_config = ConfigDict(extra="forbid")

    phase: CanonicalLifecyclePhase
    source_projection: LifecyclePhaseProjection
    resume_channel: ResumeChannel = "unknown"
    checkpoint_present: bool = False
    task_id: str = ""
    mission_id: str = ""
    source_refs: dict[str, str] = Field(default_factory=dict)


def _project_phase(
    value: Any,
    *,
    source_vocab: SourceVocabulary,
    mapping: dict[str, CanonicalLifecyclePhase],
) -> LifecyclePhaseProjection:
    raw = _coerce_status_text(value)
    return LifecyclePhaseProjection(
        source_vocab=source_vocab,
        source_value=raw,
        phase=mapping.get(raw, "unknown"),
    )


def project_task_status(value: Any) -> LifecyclePhaseProjection:
    """Project a task status value to a canonical phase."""
    return _project_phase(
        value,
        source_vocab="task_status",
        mapping=_TASK_STATUS_TO_PHASE,
    )


def project_plan_step_status(value: Any) -> LifecyclePhaseProjection:
    """Project a plan-step status value to a canonical phase."""
    return _project_phase(
        value,
        source_vocab="plan_step_status",
        mapping=_PLAN_STEP_STATUS_TO_PHASE,
    )


def project_mission_status(value: Any) -> LifecyclePhaseProjection:
    """Project a mission status value to a canonical phase."""
    return _project_phase(
        value,
        source_vocab="mission_status",
        mapping=_MISSION_STATUS_TO_PHASE,
    )


def project_working_status(value: Any) -> LifecyclePhaseProjection:
    """Project a working status value to a canonical phase."""
    return _project_phase(
        value,
        source_vocab="working_status",
        mapping=_WORKING_STATUS_TO_PHASE,
    )


def project_resume_channel(metadata: Mapping[str, Any] | None) -> ResumeChannel:
    """Derive the typed resume channel from lifecycle metadata."""
    if not isinstance(metadata, Mapping):
        return "none"
    if (
        metadata.get("cron_resume_attempt_count") is not None
        or metadata.get("cron_resume_current_interval_s") is not None
    ):
        return "cron"
    if metadata.get("persistent_service_id"):
        return "persistent_service"
    if metadata.get("delegated_to_agent_id"):
        return "delegated"
    if bool(metadata.get("awaiting_continuation_reply")):
        return "direct"
    return "none"


def resume_transition_for(
    *,
    channel: ResumeChannel,
    from_phase: CanonicalLifecyclePhase,
) -> ResumeTransition:
    """Compute the transition that firing ``channel`` would cause."""
    if from_phase in ("completed", "cancelled", "failed"):
        return ResumeTransition(
            channel=channel, from_phase=from_phase, to_phase=from_phase
        )
    if channel == "none" or channel == "unknown":
        return ResumeTransition(
            channel=channel, from_phase=from_phase, to_phase=from_phase
        )
    if channel == "direct":
        return ResumeTransition(
            channel=channel, from_phase=from_phase, to_phase="active"
        )
    return ResumeTransition(channel=channel, from_phase=from_phase, to_phase="active")


def build_unified_projection(
    *,
    source_projection: LifecyclePhaseProjection,
    resume_channel: ResumeChannel = "unknown",
    checkpoint_present: bool = False,
    task_id: str = "",
    mission_id: str = "",
    source_refs: Mapping[str, str] | None = None,
) -> UnifiedLifecycleProjection:
    """Compose a typed unified lifecycle projection from typed inputs."""
    return UnifiedLifecycleProjection(
        phase=source_projection.phase,
        source_projection=source_projection,
        resume_channel=resume_channel,
        checkpoint_present=bool(checkpoint_present),
        task_id=str(task_id or "").strip(),
        mission_id=str(mission_id or "").strip(),
        source_refs=dict(source_refs or {}),
    )


def _coerce_status_text(value: Any) -> str:
    """Coerce an enum-like or string status to its raw string form."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    inner = getattr(value, "value", None)
    if isinstance(inner, str):
        return inner.strip()
    return str(value).strip()


__all__ = [
    "CanonicalLifecyclePhase",
    "ResumeChannel",
    "SourceVocabulary",
    "LifecyclePhaseProjection",
    "ResumeTransition",
    "UnifiedLifecycleProjection",
    "project_task_status",
    "project_plan_step_status",
    "project_mission_status",
    "project_working_status",
    "project_resume_channel",
    "resume_transition_for",
    "build_unified_projection",
]
