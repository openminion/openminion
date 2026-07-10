from __future__ import annotations

from enum import StrEnum

from openminion.modules.prompting.decision import (
    BRAIN_FRESHNESS_POLICY_CONSTRAINT as _BRAIN_FRESHNESS_POLICY_CONSTRAINT,
)
from typing import Literal
from pathlib import Path

from openminion.modules.paths import (
    IDENTITY_DB_FILENAME,
    IDENTITY_DB_SUBPATH,
    MEMORY_DB_FILENAME,
    MEMORY_DB_SUBPATH,
    SESSIONS_DB_FILENAME,
    SESSION_DB_SUBPATH,
)


class BrainState(StrEnum):
    ACTIVE = "active"
    CONTINUE = "continue"
    WAITING_USER = "waiting_user"
    JOB_PENDING = "job_pending"
    DONE = "done"
    ERROR = "error"
    STOPPED = "stopped"
    FAILED = "failed"


BRAIN_STATE_ACTIVE = BrainState.ACTIVE
BRAIN_STATE_CONTINUE = BrainState.CONTINUE
BRAIN_STATE_WAITING_USER = BrainState.WAITING_USER
BRAIN_STATE_JOB_PENDING = BrainState.JOB_PENDING
BRAIN_STATE_DONE = BrainState.DONE
BRAIN_STATE_ERROR = BrainState.ERROR
BRAIN_STATE_STOPPED = BrainState.STOPPED
BRAIN_STATE_FAILED = BrainState.FAILED
BRAIN_ACTIVE_STATES: frozenset[str] = frozenset(
    (BRAIN_STATE_ACTIVE, BRAIN_STATE_CONTINUE, BRAIN_STATE_JOB_PENDING)
)
BRAIN_TERMINAL_STATES: frozenset[str] = frozenset(
    (BRAIN_STATE_DONE, BRAIN_STATE_ERROR, BRAIN_STATE_STOPPED, BRAIN_STATE_FAILED)
)


class ActionStatus(StrEnum):
    SUCCESS = "success"
    RETRY = "retry"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_USER = "needs_user"
    TIMEOUT = "timeout"


BRAIN_ACTION_STATUS_SUCCESS = ActionStatus.SUCCESS
BRAIN_ACTION_STATUS_RETRY = ActionStatus.RETRY
BRAIN_ACTION_STATUS_FAILED = ActionStatus.FAILED
BRAIN_ACTION_STATUS_BLOCKED = ActionStatus.BLOCKED
BRAIN_ACTION_STATUS_NEEDS_USER = ActionStatus.NEEDS_USER
BRAIN_ACTION_STATUS_TIMEOUT = ActionStatus.TIMEOUT
BRAIN_ACTION_STATUS_FAILURES: frozenset[str] = frozenset(
    (
        BRAIN_ACTION_STATUS_FAILED,
        BRAIN_ACTION_STATUS_BLOCKED,
        BRAIN_ACTION_STATUS_TIMEOUT,
    )
)
BRAIN_ACTION_STATUS_RETRYABLE: frozenset[str] = frozenset(
    (BRAIN_ACTION_STATUS_RETRY, BRAIN_ACTION_STATUS_NEEDS_USER)
)
CONFIRMATION_MESSAGE_ARG_LIMIT = 3
CONFIRMATION_MESSAGE_ARG_VALUE_LIMIT = 120
TOOL_MESSAGE_DEPTH_LIMIT = 3
TOOL_MESSAGE_SEQUENCE_LIMIT = 8
TOOL_MESSAGE_STRING_LIMIT = 1200

BRAIN_FRESHNESS_POLICY_CONSTRAINT = _BRAIN_FRESHNESS_POLICY_CONSTRAINT


class ExecutionOutcome(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    NEEDS_USER = "needs_user"


BRAIN_EXECUTION_OUTCOME_PENDING = ExecutionOutcome.PENDING
BRAIN_EXECUTION_OUTCOME_IN_PROGRESS = ExecutionOutcome.IN_PROGRESS
BRAIN_EXECUTION_OUTCOME_RETRYING = ExecutionOutcome.RETRYING
BRAIN_EXECUTION_OUTCOME_SUCCEEDED = ExecutionOutcome.SUCCEEDED
BRAIN_EXECUTION_OUTCOME_FAILED = ExecutionOutcome.FAILED
BRAIN_EXECUTION_OUTCOME_BLOCKED = ExecutionOutcome.BLOCKED
BRAIN_EXECUTION_OUTCOME_SKIPPED = ExecutionOutcome.SKIPPED
BRAIN_EXECUTION_OUTCOME_NEEDS_USER = ExecutionOutcome.NEEDS_USER


class DecisionRoute(StrEnum):
    RESPOND = "respond"
    ACT = "act"


BRAIN_DECISION_ROUTE_RESPOND = DecisionRoute.RESPOND
BRAIN_DECISION_ROUTE_ACT = DecisionRoute.ACT


class RespondKind(StrEnum):
    ANSWER = "answer"
    CLARIFY = "clarify"


BRAIN_RESPOND_KIND_ANSWER = RespondKind.ANSWER
BRAIN_RESPOND_KIND_CLARIFY = RespondKind.CLARIFY


class ActProfile(StrEnum):
    GENERAL = "general"
    CODING = "coding"
    RESEARCH = "research"
    ORCHESTRATE = "orchestrate"


BRAIN_ACT_PROFILE_GENERAL = ActProfile.GENERAL
BRAIN_ACT_PROFILE_CODING = ActProfile.CODING
BRAIN_ACT_PROFILE_RESEARCH = ActProfile.RESEARCH
BRAIN_ACT_PROFILE_ORCHESTRATE = ActProfile.ORCHESTRATE


class ExecutionTarget(StrEnum):
    LOCAL = "local"
    DELEGATED = "delegated"


BRAIN_EXECUTION_TARGET_LOCAL = ExecutionTarget.LOCAL
BRAIN_EXECUTION_TARGET_DELEGATED = ExecutionTarget.DELEGATED


class InternalMode(StrEnum):
    ACT_ADAPTIVE = "act_loop_adaptive"
    ACT_CODING = "act_profile_coding"
    ACT_RESEARCH = "act_profile_research"
    ACT_ORCHESTRATE = "act:orchestrate"
    EXECUTION_TARGET_DELEGATED = "execution_target_delegated"
    LOOP_PHASE_OBSERVE = "loop_phase_observe"
    LOOP_PHASE_EVAL = "loop_phase_eval"
    LOOP_PHASE_REFINE = "loop_phase_refine"


BRAIN_INTERNAL_MODE_ACT_ADAPTIVE = InternalMode.ACT_ADAPTIVE
BRAIN_INTERNAL_MODE_ACT_CODING = InternalMode.ACT_CODING
BRAIN_INTERNAL_MODE_ACT_RESEARCH = InternalMode.ACT_RESEARCH
BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE = InternalMode.ACT_ORCHESTRATE
BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED = InternalMode.EXECUTION_TARGET_DELEGATED
BRAIN_INTERNAL_MODE_LOOP_PHASE_OBSERVE = InternalMode.LOOP_PHASE_OBSERVE
BRAIN_INTERNAL_MODE_LOOP_PHASE_EVAL = InternalMode.LOOP_PHASE_EVAL
BRAIN_INTERNAL_MODE_LOOP_PHASE_REFINE = InternalMode.LOOP_PHASE_REFINE


class CommandKind(StrEnum):
    TOOL = "tool"
    AGENT = "agent"
    ASK_USER = "ask_user"
    FINISH = "finish"
    THINK = "think"


BRAIN_COMMAND_KIND_TOOL = CommandKind.TOOL
BRAIN_COMMAND_KIND_AGENT = CommandKind.AGENT
BRAIN_COMMAND_KIND_ASK_USER = CommandKind.ASK_USER
BRAIN_COMMAND_KIND_FINISH = CommandKind.FINISH
BRAIN_COMMAND_KIND_THINK = CommandKind.THINK


class Disposition(StrEnum):
    CLOSE = "close"
    CONTINUE = "continue"
    REPLAN = "replan"


BRAIN_DISPOSITION_CLOSE = Disposition.CLOSE
BRAIN_DISPOSITION_CONTINUE = Disposition.CONTINUE
BRAIN_DISPOSITION_REPLAN = Disposition.REPLAN
BRAIN_DISPOSITIONS_RETRYING: frozenset[str] = frozenset(
    (BRAIN_DISPOSITION_CONTINUE, BRAIN_DISPOSITION_REPLAN)
)


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


BRAIN_JOB_STATUS_PENDING = JobStatus.PENDING
BRAIN_JOB_STATUS_RUNNING = JobStatus.RUNNING
BRAIN_JOB_STATUS_DONE = JobStatus.DONE
BRAIN_JOB_STATUS_FAILED = JobStatus.FAILED


class ConfirmationResponse(StrEnum):
    AFFIRM = "affirm"
    DENY = "deny"
    UNCLEAR = "unclear"


BRAIN_CONFIRM_RESPONSE_AFFIRM = ConfirmationResponse.AFFIRM
BRAIN_CONFIRM_RESPONSE_DENY = ConfirmationResponse.DENY
BRAIN_CONFIRM_RESPONSE_UNCLEAR = ConfirmationResponse.UNCLEAR


class MissionStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    AWAITING_ASYNC = "awaiting_async"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    HALTED = "halted"


BRAIN_MISSION_STATUS_ACTIVE = MissionStatus.ACTIVE
BRAIN_MISSION_STATUS_PAUSED = MissionStatus.PAUSED
BRAIN_MISSION_STATUS_AWAITING_ASYNC = MissionStatus.AWAITING_ASYNC
BRAIN_MISSION_STATUS_COMPLETED = MissionStatus.COMPLETED
BRAIN_MISSION_STATUS_CANCELLED = MissionStatus.CANCELLED
BRAIN_MISSION_STATUS_HALTED = MissionStatus.HALTED


class MissionJudgment(StrEnum):
    COMPLETE = "complete"
    CONTINUE = "continue"
    ASK_USER = "ask_user"
    HALT = "halt"


BRAIN_MISSION_JUDGMENT_COMPLETE = MissionJudgment.COMPLETE
BRAIN_MISSION_JUDGMENT_CONTINUE = MissionJudgment.CONTINUE
BRAIN_MISSION_JUDGMENT_ASK_USER = MissionJudgment.ASK_USER
BRAIN_MISSION_JUDGMENT_HALT = MissionJudgment.HALT


class MissionRoute(StrEnum):
    ORDINARY = "ordinary"
    START = "start"
    CONTINUE = "continue"
    REVISE = "revise"
    FINISH = "finish"
    PAUSE = "pause"
    CANCEL = "cancel"
    FORK = "fork"


BRAIN_MISSION_ROUTE_ORDINARY = MissionRoute.ORDINARY
BRAIN_MISSION_ROUTE_START = MissionRoute.START
BRAIN_MISSION_ROUTE_CONTINUE = MissionRoute.CONTINUE
BRAIN_MISSION_ROUTE_REVISE = MissionRoute.REVISE
BRAIN_MISSION_ROUTE_FINISH = MissionRoute.FINISH
BRAIN_MISSION_ROUTE_PAUSE = MissionRoute.PAUSE
BRAIN_MISSION_ROUTE_CANCEL = MissionRoute.CANCEL
BRAIN_MISSION_ROUTE_FORK = MissionRoute.FORK


class ResetPolicy(StrEnum):
    ORDINARY = "ordinary_new_turn"
    MISSION_START = "mission_start"
    MISSION_CONTINUE = "mission_continue"
    MISSION_REVISE = "mission_revise"
    MISSION_FINISH = "mission_finish"
    MISSION_FORK = "mission_fork"
    CONFIRMATION = "confirmation_resume"


BRAIN_RESET_POLICY_ORDINARY = ResetPolicy.ORDINARY
BRAIN_RESET_POLICY_MISSION_START = ResetPolicy.MISSION_START
BRAIN_RESET_POLICY_MISSION_CONTINUE = ResetPolicy.MISSION_CONTINUE
BRAIN_RESET_POLICY_MISSION_REVISE = ResetPolicy.MISSION_REVISE
BRAIN_RESET_POLICY_MISSION_FINISH = ResetPolicy.MISSION_FINISH
BRAIN_RESET_POLICY_MISSION_FORK = ResetPolicy.MISSION_FORK
BRAIN_RESET_POLICY_CONFIRMATION = ResetPolicy.CONFIRMATION

MEMORY_CONSOLIDATION_MODULE_STATE_KEY = "memory_consolidation"
WATCH_MODULE_STATE_KEY = "watch_subscription"
CODING_MODULE_STATE_KEY = "coding"
CODING_PUBLIC_TAG = "[act:coding]"
RESEARCH_PUBLIC_TAG = "[act:research]"

CONTEXT_BUDGET_TIER_SHORT = "short"
CONTEXT_BUDGET_TIER_MEDIUM = "medium"
CONTEXT_BUDGET_TIER_FULL = "full"

SKILL_SELECTION_REASON_DIRECT = "direct_config"
SKILL_SELECTION_REASON_DIRECT_NAMED = "direct_named"
SKILL_SELECTION_REASON_DIRECT_SINGLE_CATALOG = "direct_single_catalog"
SKILL_SELECTION_REASON_LLM = "llm_select"
SKILL_SELECTION_REASON_RETRIEVAL = "retrieval_select"
SKILL_SELECTION_MODEL_UNAVAILABLE = "model_unavailable"
SKILL_SELECTION_PARSE_ERROR = "parse_error"
SKILL_SELECTION_INVALID_SKILL_ID = "invalid_skill_id"
SKILL_SELECTION_TIMEOUT = "timeout"
SKILL_SELECTION_RATE_LIMITED = "rate_limited"
DUPLICATE_BATCH_RECOVERY_LIMIT = 1
TOOL_OUTCOME_STAGED_COUNT_KEY = "staged_count"
DELEGATION_TEXT_MAX_CHARS = 800
DELEGATION_ARTIFACT_REF_LIMIT = 8

AUTONOMOUS_CONTINUATION_STOPPED_CAUSE_MAX_CHARS = 500

DEFAULT_CONFIG_FILENAMES = (
    "brain.yaml",
    "brain.yml",
)
DEFAULT_CONFIG_FILENAME = DEFAULT_CONFIG_FILENAMES[0]
DEFAULT_INTEGRATED_CONFIG_SUBDIR = Path("brain")
DEFAULT_SESSION_DB_FILENAME = SESSIONS_DB_FILENAME
DEFAULT_SESSION_DB_SUBPATH = SESSION_DB_SUBPATH
DEFAULT_IDENTITY_DB_FILENAME = IDENTITY_DB_FILENAME
DEFAULT_IDENTITY_DB_SUBPATH = IDENTITY_DB_SUBPATH
DEFAULT_MEMORY_DB_FILENAME = MEMORY_DB_FILENAME
DEFAULT_MEMORY_DB_SUBPATH = MEMORY_DB_SUBPATH

STOP_BUDGET_EXHAUSTED = "budget_exhausted"
STOP_SESSION_EXTENSIONS_EXHAUSTED = "session_extensions_exhausted"
STOP_NOOP_GUARD = "budget_noop_guard"
STOP_TOKEN_BUDGET_EXHAUSTED = "token_budget_exhausted"
STOP_HARD_CAP = "hard_cap_reached"
STOP_USER_DECLINED = "user_declined_extension"
STOP_USER_TIMEOUT = "user_timeout"

# Bounded-text caps for memory-write helpers in `execution/memory.py`.
DECISION_RATIONALE_MAX_CHARS = 280

# ALCC plan-reconciliation constants. The diagnostic cap bounds persisted
# closure-event payloads for pathological plans.
PLAN_RECONCILIATION_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "blocked", "cancelled"}
)
PLAN_RECONCILIATION_INCOMPLETE_REASON = "plan_reconciliation_incomplete"
PLAN_RECONCILIATION_STEP_ID_DIAG_CAP = 24

# Structural review analyzer thresholds. Pinned centrally so tuning is a
# deliberate brain-owner change rather than an analyzer-local drift.
REVIEW_DIFF_LARGE_DELETION_THRESHOLD = 50
REVIEW_DIFF_MANY_FILES_THRESHOLD = 8

# Brain-local state keys live here; shared keys stay in `openminion.base.constants`.
STATE_KEY_MODULE_STATE = "module_state"
STATE_KEY_TASK_BACKED_RESUME = "task_backed_resume_state"
STATE_KEY_NEXT_ATTEMPT = "next_attempt_state"
EVENT_NAME_ADAPTIVE_STATUS = "emit_adaptive_status"

RVRH_DEFAULT_CONFIDENCE_THRESHOLD: float = 0.6
RVRH_DEFAULT_FRESHNESS_CAP_SECONDS: int | None = None

# VGD: action-approval-verifier rationale constants. Closed-set; widen via review.
VGD_TIMEOUT_ESCALATE_RATIONALE = "verifier_timeout_escalate"
VGD_DEFAULT_TIMEOUT_SECONDS: int = 3

RespondKindLiteral = Literal["assistant", "policy_confirmation_prompt"]
RESPOND_KIND_ASSISTANT: RespondKindLiteral = "assistant"
RESPOND_KIND_POLICY_CONFIRMATION_PROMPT: RespondKindLiteral = (
    "policy_confirmation_prompt"
)
RESPOND_KIND_VALUES: frozenset[str] = frozenset(
    {RESPOND_KIND_ASSISTANT, RESPOND_KIND_POLICY_CONFIRMATION_PROMPT}
)
# PCHC: typed event name for policy-confirmation prompt audit records.
SESSION_EVENT_POLICY_CONFIRMATION_PROMPT = "policy_confirmation_prompt"
