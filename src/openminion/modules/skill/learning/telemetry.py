"""Telemetry payload helpers for workflow-learning lifecycle events."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from openminion.modules.telemetry.events import catalog as event_catalog

WORKFLOW_SHAPE_MINED = "skill.workflow_shape.mined"
WORKFLOW_CANDIDATE_PROPOSED = "skill.workflow_candidate.proposed"
WORKFLOW_REPLAY_PROOF_RECORDED = "skill.workflow_replay.proof_recorded"
WORKFLOW_PROPOSAL_APPLIED = "skill.workflow_proposal.applied"
WORKFLOW_SKILL_REUSED = "skill.workflow_skill.reused"
WORKFLOW_TRUST_PROMOTED = "skill.workflow_trust.promoted"
WORKFLOW_TRUST_DOWNGRADED = "skill.workflow_trust.downgraded"

WORKFLOW_LEARNING_EVENT_TYPES: frozenset[str] = frozenset(
    {
        WORKFLOW_SHAPE_MINED,
        WORKFLOW_CANDIDATE_PROPOSED,
        WORKFLOW_REPLAY_PROOF_RECORDED,
        WORKFLOW_PROPOSAL_APPLIED,
        WORKFLOW_SKILL_REUSED,
        WORKFLOW_TRUST_PROMOTED,
        WORKFLOW_TRUST_DOWNGRADED,
    }
)

_SECRET_FIELDS = {"raw_transcript", "transcript", "secret", "token", "password"}


def _safe_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in payload.items():
        normalized_key = str(key or "")
        if normalized_key.lower() in _SECRET_FIELDS:
            continue
        if isinstance(value, str) and value.startswith(("/", "~")):
            safe[normalized_key] = "<path>"
        else:
            safe[normalized_key] = value
    return safe


def workflow_learning_event(event_type: str, **payload: Any) -> dict[str, Any]:
    """Build a registered, redacted workflow-learning telemetry event."""

    registered = event_catalog.register_event_type(event_type, strict=True)
    return {"event_type": registered, "payload": _safe_payload(payload)}


__all__ = (
    "WORKFLOW_CANDIDATE_PROPOSED",
    "WORKFLOW_LEARNING_EVENT_TYPES",
    "WORKFLOW_PROPOSAL_APPLIED",
    "WORKFLOW_REPLAY_PROOF_RECORDED",
    "WORKFLOW_SHAPE_MINED",
    "WORKFLOW_SKILL_REUSED",
    "WORKFLOW_TRUST_DOWNGRADED",
    "WORKFLOW_TRUST_PROMOTED",
    "workflow_learning_event",
)
