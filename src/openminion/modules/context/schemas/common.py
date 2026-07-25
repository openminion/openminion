# ruff: noqa: F401
import hashlib
import json
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .. import constants as _context_constants

from openminion.modules.task.plan import (  # noqa: F401
    TaskPlan,
    TaskPlanDifficulty,
    TaskPlanRevision,
    TaskPlanStatus,
    TaskPlanStep,
    TaskPlanStepBlocked,
    TaskPlanStepCompleted,
    TaskPlanStepStatus,
    TaskPlanTerminalSignal,
    TaskPlanToolFamily,
)

Purpose = Literal[
    "decide", "plan", "act", "reflect", "summarize", "judge", "validate", "chat"
]
MessageRole = Literal["system", "developer", "user", "assistant", "tool"]
ARTIFACT_PREVIEW_MAX_CHARS = _context_constants.ARTIFACT_PREVIEW_MAX_CHARS
ARTIFACT_PREVIEW_MAX_BULLETS = _context_constants.ARTIFACT_PREVIEW_MAX_BULLETS
PINNED_BUCKETS = _context_constants.PINNED_BUCKETS
TRIM_ORDER = _context_constants.TRIM_ORDER
TASK_PLAN_OUTPUT_SUMMARY_MAX_CHARS = (
    _context_constants.TASK_PLAN_OUTPUT_SUMMARY_MAX_CHARS
)
TASK_PLAN_TOOL_FAMILIES = _context_constants.TASK_PLAN_TOOL_FAMILIES


ContextBudgetTier = Literal["short", "medium", "full"]
ContextDecisionTracePersistenceStatus = Literal["pending", "persisted", "degraded"]
SegmentBucket = Literal[
    "static_prefix",
    "mission_snapshot",
    "budget_telemetry",
    "summaries",
    "conversation_summary",
    "active_plan",
    "task_digest",
    "trailer_feedback",
    "self_awareness",
    "recent_window",
    "memory",
    "retrieval",
    "evidence_refs",
    "turn_input",
]

BlockPriority = Literal["P0", "P1", "P2", "P3", "P4"]
BlockType = Literal[
    "identity",
    "safety",
    "task_header",
    "summary",
    "continuation",
    "active_state",
    "facts",
    "memory",
    "skills",
    "artifacts",
    "instructions",
    "dialogue",
    "tool_events",
    "retrieval",
]

ContextTracePersistenceReason = Literal[
    "persisted_canonical",
    "persisted_fallback",
    "canonical_failed",
    "fallback_failed",
    "no_persistence_sink",
    "not_attempted",
]

CONTEXT_DECISION_TRACE_VERSION = _context_constants.CONTEXT_DECISION_TRACE_VERSION
CONTEXT_DECISION_TRACE_MAX_REFERENCES = (
    _context_constants.CONTEXT_DECISION_TRACE_MAX_REFERENCES
)
CONTEXT_DECISION_TRACE_MAX_BYTES = _context_constants.CONTEXT_DECISION_TRACE_MAX_BYTES


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
