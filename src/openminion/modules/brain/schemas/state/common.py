# ruff: noqa: F401
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ...constants import RESPOND_KIND_ASSISTANT, MissionStatus, RespondKindLiteral
from ..base import ActionStatus, ArtifactRef, iso_now, new_uuid
from ..commands import Command
from ..decisions import ClarifyContext, PendingTurnContext, RequestReadiness
from ..freshness import FreshnessContract, FreshnessDiagnostics, FreshnessObligations
from ..plan import (
    AdaptiveRevisionCheckpoint,
    FailureType,
    FeasibilityReport,
    FixItem,
    IntentExecutionState,
    Plan,
    ProgressCheckpointReport,
    ReflectOutcome,
    StepRiskAssessment,
    SubIntent,
    build_intent_execution_states,
    feasibility_report_payload,
    normalize_sub_intent_ids,
    select_sub_intents_by_ids,
    sub_intent_descriptions,
    to_structured_sub_intents,
)

CognitionTier = Literal["T0_direct", "T1_light", "T2_tool", "T3_high_assurance"]
WorkingStatus = Literal[
    "active",
    "continue",
    "waiting_user",
    "job_pending",
    "done",
    "error",
    "stopped",
]
MissionLifecycleStatus = MissionStatus
MissionJudgmentOutcome = Literal["complete", "continue", "ask_user", "halt"]
PostActionJudgmentOutcome = Literal[
    "advance",
    "retry",
    "replan",
    "ask_user",
    "halt",
    "skip",
]
PermissionMode = Literal[
    "ask",
    "auto",
    "bypass",
    "readonly",
    "plan",
    "default",
    "acceptEdits",
    "bypassPermissions",
]
RunSubstate = Literal[
    "INTERPRET",
    "CLARIFY",
    "DECIDE",
    "PLAN",
    "APPROVE",
    "ACT",
    "OBSERVE",
    "VERIFY",
    "REFLECT",
    "IMPROVE",
    "COMPACT",
    "RESPOND",
]
ClarifyQuestionType = Literal[
    "missing_field",
    "ambiguous_input",
    "risk_confirmation",
    "constraint_check",
    "tool_permission",
]
BudgetEnvelopeStatus = Literal["comfortable", "tight", "near_exhaustion"]
LearningLoopMetricReadiness = Literal["ready", "partial"]
