"""Canonical telemetry event-type catalog for runtime-owned event strings."""

COMPONENT_STARTED = "component.started"
COMPONENT_STOPPED = "component.stopped"
COMPONENT_CRASHED = "component.crashed"
COMPONENT_HEARTBEAT = "component.heartbeat"
COMPONENT_DEGRADED = "component.degraded"
COMPONENT_RECOVERED = "component.recovered"
COMPONENT_RESTART_REQUESTED = "component.restart_requested"
COMPONENT_RESTART_SUCCEEDED = "component.restart_succeeded"
COMPONENT_RESTART_FAILED = "component.restart_failed"

STORAGE_POOL_STATS = "storage.pool.stats"
STORAGE_QUERY = "storage.query"
STORAGE_ERROR_CLASS = "storage.error_class"
STORAGE_SLOW_QUERY = "storage.slow_query"
STORAGE_MIGRATION = "storage.migration"

AGENT_BOUND = "agent.bound"
AGENT_SWITCHED = "agent.switched"
CLIENT_ATTACH = "client.attach"
CLIENT_DETACHED = "client.detached"
CLIENT_EXIT_CLEAN = "client.exit_clean"
SESSION_CLOSED = "session.closed"
SESSION_COMPACTION_ARCHIVE = "session.compaction.archive"
SESSION_CONTEXT_BUDGET = "session.context.budget"
SESSION_CREATED = "session.created"
SESSION_EXPIRED = "session.expired"
SESSION_RESUMED = "session.resumed"
SESSION_STALE = "session.stale"
SESSION_STATUS_CHANGED = "session.status.changed"
SESSION_SUMMARY_ENRICHED = "session.summary.enriched"

RESPONSE_ACKED = "response.acked"
RESPONSE_DELIVERED = "response.delivered"
RESPONSE_PERSISTED = "response.persisted"
RESPONSE_SUPPRESSED = "response.suppressed"
TRAILER_EMITTED = "trailer.emitted"
TRAILER_EXPECTED = "trailer.expected"
TRAILER_FEEDBACK_PENDING = "trailer.feedback_pending"

LLM_CACHE_METRICS = "llm.cache.metrics"
LLM_CALL_COMPLETED = "llm.call.completed"
LLM_CALL_STARTED = "llm.call.started"
LLM_REQUEST_STARTED = "llm.request.started"
LLM_CALL_LEGACY = "llm_call"

MEMORY_CANDIDATE_DELETE = "memory.candidate.delete"
MEMORY_CANDIDATE_PROMOTE = "memory.candidate.promote"
MEMORY_CANDIDATE_PUT = "memory.candidate.put"
MEMORY_CANDIDATE_UPDATE = "memory.candidate.update"
MEMORY_CAPSULE_REFRESHED = "memory.capsule.refreshed"
MEMORY_CAPSULE_REFRESH_FAILED = "memory.capsule.refresh_failed"
MEMORY_CAPSULE_REFRESH_SKIPPED = "memory.capsule.refresh_skipped"
MEMORY_CONTEXT_BUILT = "memory.context.built"
MEMORY_CONTEXT_FAILED = "memory.context.failed"
MEMORY_FOLLOWUP_COMPLETED = "memory.followup.completed"
MEMORY_FOLLOWUP_FAILED = "memory.followup.failed"
MEMORY_FOLLOWUP_PENDING = "memory.followup.pending"
MEMORY_POLICY_SNAPSHOT = "memory.policy.snapshot"
MEMORY_RECORD_DELETE = "memory.record.delete"
MEMORY_RECORD_FEEDBACK = "memory.record.feedback"
MEMORY_RECORD_INVALIDATE = "memory.record.invalidate"
MEMORY_RECORD_PUT = "memory.record.put"
MEMORY_RECORD_SUPERSEDE = "memory.record.supersede"
MEMORY_RECORD_TOMBSTONE = "memory.record.tombstone"
MEMORY_RECORD_UPSERT = "memory.record.upsert"
MEMORY_RELATION_PUT = "memory.relation.put"
MEMORY_RETRIEVAL_BUILT = "memory.retrieval.built"
MEMORY_TIER_TRANSITION_PUT = "memory.tier_transition.put"
MEMORY_TURN_RECORDED = "memory.turn.recorded"
MEMORY_TURN_RECORD_FAILED = "memory.turn.record_failed"
MEMORY_WRITE_COMPLETED = "memory.write.completed"
MEMORY_WRITE_FAILED = "memory.write.failed"
MEMORY_WRITE_STARTED = "memory.write.started"

MEMORY_PROMOTION_EVALUATED = "memory.promotion.evaluated"
MEMORY_TIER_TRANSITION_APPLIED = "memory.tier_transition.applied"
MEMORY_OUTCOME_FEEDBACK_APPLIED = "memory.outcome_feedback.applied"
MEMORY_RETRIEVAL_POLICY_DECIDED = "memory.retrieval.policy_decided"
MEMORY_RETRIEVAL_CAPPED = "memory.retrieval.limit_applied"
MEMORY_GC_COMPLETED = "memory.gc.completed"
MEMORY_CONFIDENCE_DECAYED = "memory.confidence.decayed"
MEMORY_SCOPE_CAPACITY_EVICTED = "memory.scope_capacity.evicted"
MEMORY_SOFT_DELETED_PURGED = "memory.soft_deleted.purged"
MEMORY_SUMMARY_COMPRESSED = "memory.summary.compressed"

BRAIN_TUNING_ADJUSTED = "brain.threshold_adjustment"
TASK_PLAN_ABANDONED = "task_plan.abandoned"
TASK_PLAN_COMPLETED = "task_plan.completed"
TASK_PLAN_DECLARED = "task_plan.declared"
TASK_PLAN_INVALID_TRAILER = "task_plan.invalid_trailer"
TASK_PLAN_REVISED = "task_plan.revised"
TASK_PLAN_STEP_BLOCKED = "task_plan.step_blocked"
TASK_PLAN_STEP_COMPLETED = "task_plan.step_completed"
THREAD_DECISION = "thread.decision"
WM_UPDATED = "wm.updated"

BRAIN_GOAL_RISK_RESOLVED = "brain.goal.risk_resolved"
BRAIN_GOAL_STAGED = "brain.goal.staged"
BRAIN_GOAL_REVISION_STAGED = "brain.goal.revision_staged"
BRAIN_META_RULE_STAGED = "brain.meta_rule.staged"
BRAIN_SAFETY_PREEMPTED = "brain.safety.preempted"
BRAIN_STRATEGY_BUDGET_CONSUMED = "brain.strategy.budget_consumed"
BRAIN_FAILURE_PATTERN_OBSERVED = "brain.failure.pattern_observed"
BRAIN_LEARNING_ATTRIBUTED = "brain.learning.attributed"
BRAIN_KNOWLEDGE_CONSOLIDATED = "brain.knowledge.consolidated"
BRAIN_LLM_OVERRIDE_REJECTED = "brain.llm.override_rejected"
BRAIN_MISSION_COMPLETED = "brain.mission.completed"
BRAIN_MISSION_HALTED = "brain.mission.halted"
GOAL_CRON_ADVANCED = "goal.cron.advanced"
GOAL_RESUME_CONTEXT_LOADED = "goal.resume_context.loaded"

MRDD_REGROUNDING_INJECT = "mrdd_regrounding_inject"
MRDD_DRIFT_SIGNAL = "mrdd_drift_signal"
MRDD_HOOK_ERROR = "mrdd_hook_error"

RVRH_RECALL_DECISION = "rvrh_recall_decision"

PROMPT_CACHE_OBSERVATION = "prompt_cache_observation"

MSPO_MEMORY_SPAN_READ = "mspo_memory_span_read"

CHAT_PHASE_TIMING = "chat.phase_timing"

SFRX_SESSION_FORK = "sfrx_session_fork"
SFRX_FILE_RESTORE = "sfrx_file_restore"

TOOL_RUN = "tool.run"
TOOL_CALL_LEGACY = "tool_call"
TOOL_ENVELOPE_REPAIR_RETRY = "tool_envelope.repair_retry"
TOOL_ENVELOPE_REPAIR_EXHAUSTED = "tool_envelope.repair_exhausted"

PAE_IDLE_TICK_CANCELLED = "pae.idle_tick.cancelled"
PAE_IDLE_TICK_SCHEDULED = "pae.idle_tick.scheduled"
PAE_IDLE_TICK_SUPPRESSED = "pae.idle_tick.suppressed"
RLM_TICK_COMPLETED = "rlm.tick.completed"
RLM_TICK_STARTED = "rlm.tick.started"
TICK = "tick"

CRON_ANNOUNCE = "cron.announce"
RUN_CANCEL_REQUESTED = "run.cancel_requested"

AUTH_DENIED = "auth_denied"
APPROVAL_REQUIRED = "approval_required"
POLICY_DENIED = "policy_denied"
SECRET_REDACTED = "secret_redacted"
SECURITY_WARNING = "security_warning"
CONTEXT_MANIFEST = "context.manifest"
CONTEXT_MANIFEST_CREATED = "context.manifest.created"
CONTEXT_PACK = "context_pack"

METRIC = "metric"
MESSAGE = "message"
MODULE_DEBUG_FAILURE = "module.debug.failure"
MODULE_STATS = "module.stats"
SUMMARY_UPDATED = "summary.updated"


EVENT_TYPES: frozenset[str] = frozenset(
    {
        COMPONENT_STARTED,
        COMPONENT_STOPPED,
        COMPONENT_CRASHED,
        COMPONENT_HEARTBEAT,
        COMPONENT_DEGRADED,
        COMPONENT_RECOVERED,
        COMPONENT_RESTART_REQUESTED,
        COMPONENT_RESTART_SUCCEEDED,
        COMPONENT_RESTART_FAILED,
        STORAGE_POOL_STATS,
        STORAGE_QUERY,
        STORAGE_ERROR_CLASS,
        STORAGE_SLOW_QUERY,
        STORAGE_MIGRATION,
        AGENT_BOUND,
        AGENT_SWITCHED,
        CLIENT_ATTACH,
        CLIENT_DETACHED,
        CLIENT_EXIT_CLEAN,
        SESSION_CLOSED,
        SESSION_COMPACTION_ARCHIVE,
        SESSION_CONTEXT_BUDGET,
        SESSION_CREATED,
        SESSION_EXPIRED,
        SESSION_RESUMED,
        SESSION_STALE,
        SESSION_STATUS_CHANGED,
        SESSION_SUMMARY_ENRICHED,
        RESPONSE_ACKED,
        RESPONSE_DELIVERED,
        RESPONSE_PERSISTED,
        RESPONSE_SUPPRESSED,
        TRAILER_EMITTED,
        TRAILER_EXPECTED,
        TRAILER_FEEDBACK_PENDING,
        LLM_CACHE_METRICS,
        LLM_CALL_COMPLETED,
        LLM_CALL_STARTED,
        LLM_REQUEST_STARTED,
        LLM_CALL_LEGACY,
        MEMORY_CANDIDATE_DELETE,
        MEMORY_CANDIDATE_PROMOTE,
        MEMORY_CANDIDATE_PUT,
        MEMORY_CANDIDATE_UPDATE,
        MEMORY_CAPSULE_REFRESHED,
        MEMORY_CAPSULE_REFRESH_FAILED,
        MEMORY_CAPSULE_REFRESH_SKIPPED,
        MEMORY_CONTEXT_BUILT,
        MEMORY_CONTEXT_FAILED,
        MEMORY_FOLLOWUP_COMPLETED,
        MEMORY_FOLLOWUP_FAILED,
        MEMORY_FOLLOWUP_PENDING,
        MEMORY_POLICY_SNAPSHOT,
        MEMORY_RECORD_DELETE,
        MEMORY_RECORD_FEEDBACK,
        MEMORY_RECORD_INVALIDATE,
        MEMORY_RECORD_PUT,
        MEMORY_RECORD_SUPERSEDE,
        MEMORY_RECORD_TOMBSTONE,
        MEMORY_RECORD_UPSERT,
        MEMORY_RELATION_PUT,
        MEMORY_RETRIEVAL_BUILT,
        MEMORY_TIER_TRANSITION_PUT,
        MEMORY_TURN_RECORDED,
        MEMORY_TURN_RECORD_FAILED,
        MEMORY_WRITE_COMPLETED,
        MEMORY_WRITE_FAILED,
        MEMORY_WRITE_STARTED,
        MEMORY_PROMOTION_EVALUATED,
        MEMORY_TIER_TRANSITION_APPLIED,
        MEMORY_OUTCOME_FEEDBACK_APPLIED,
        MEMORY_RETRIEVAL_POLICY_DECIDED,
        MEMORY_RETRIEVAL_CAPPED,
        MEMORY_GC_COMPLETED,
        MEMORY_CONFIDENCE_DECAYED,
        MEMORY_SCOPE_CAPACITY_EVICTED,
        MEMORY_SOFT_DELETED_PURGED,
        MEMORY_SUMMARY_COMPRESSED,
        BRAIN_TUNING_ADJUSTED,
        TASK_PLAN_ABANDONED,
        TASK_PLAN_COMPLETED,
        TASK_PLAN_DECLARED,
        TASK_PLAN_INVALID_TRAILER,
        TASK_PLAN_REVISED,
        TASK_PLAN_STEP_BLOCKED,
        TASK_PLAN_STEP_COMPLETED,
        THREAD_DECISION,
        WM_UPDATED,
        BRAIN_GOAL_RISK_RESOLVED,
        BRAIN_GOAL_STAGED,
        BRAIN_GOAL_REVISION_STAGED,
        BRAIN_META_RULE_STAGED,
        BRAIN_SAFETY_PREEMPTED,
        BRAIN_STRATEGY_BUDGET_CONSUMED,
        BRAIN_FAILURE_PATTERN_OBSERVED,
        BRAIN_LEARNING_ATTRIBUTED,
        BRAIN_KNOWLEDGE_CONSOLIDATED,
        BRAIN_LLM_OVERRIDE_REJECTED,
        BRAIN_MISSION_COMPLETED,
        BRAIN_MISSION_HALTED,
        GOAL_CRON_ADVANCED,
        GOAL_RESUME_CONTEXT_LOADED,
        MRDD_REGROUNDING_INJECT,
        MRDD_DRIFT_SIGNAL,
        MRDD_HOOK_ERROR,
        RVRH_RECALL_DECISION,
        PROMPT_CACHE_OBSERVATION,
        MSPO_MEMORY_SPAN_READ,
        CHAT_PHASE_TIMING,
        SFRX_SESSION_FORK,
        SFRX_FILE_RESTORE,
        TOOL_RUN,
        TOOL_CALL_LEGACY,
        TOOL_ENVELOPE_REPAIR_RETRY,
        TOOL_ENVELOPE_REPAIR_EXHAUSTED,
        PAE_IDLE_TICK_CANCELLED,
        PAE_IDLE_TICK_SCHEDULED,
        PAE_IDLE_TICK_SUPPRESSED,
        RLM_TICK_COMPLETED,
        RLM_TICK_STARTED,
        TICK,
        CRON_ANNOUNCE,
        RUN_CANCEL_REQUESTED,
        AUTH_DENIED,
        APPROVAL_REQUIRED,
        POLICY_DENIED,
        SECRET_REDACTED,
        SECURITY_WARNING,
        CONTEXT_MANIFEST,
        CONTEXT_MANIFEST_CREATED,
        CONTEXT_PACK,
        METRIC,
        MESSAGE,
        MODULE_DEBUG_FAILURE,
        MODULE_STATS,
        SUMMARY_UPDATED,
    }
)


LIFECYCLE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        COMPONENT_STARTED,
        COMPONENT_STOPPED,
        COMPONENT_CRASHED,
        COMPONENT_HEARTBEAT,
        COMPONENT_DEGRADED,
        COMPONENT_RECOVERED,
        COMPONENT_RESTART_REQUESTED,
        COMPONENT_RESTART_SUCCEEDED,
        COMPONENT_RESTART_FAILED,
    }
)


class UnknownEventTypeError(ValueError):
    """Raised when strict event-type registration sees an unknown name."""


def register_event_type(name: str, *, strict: bool = False) -> str:
    """Return a registered event type or raise in strict mode."""
    normalized = str(name or "").strip()
    if not normalized:
        raise UnknownEventTypeError("event_type name is empty")
    if normalized in EVENT_TYPES:
        return normalized
    if strict:
        raise UnknownEventTypeError(
            f"unregistered telemetry event_type: {normalized!r}; "
            "register it in modules/telemetry/events/catalog.py:EVENT_TYPES"
        )
    return normalized


__all__ = [
    "EVENT_TYPES",
    "LIFECYCLE_EVENT_TYPES",
    "UnknownEventTypeError",
    "register_event_type",
    "COMPONENT_STARTED",
    "COMPONENT_STOPPED",
    "COMPONENT_CRASHED",
    "COMPONENT_HEARTBEAT",
    "COMPONENT_DEGRADED",
    "COMPONENT_RECOVERED",
    "COMPONENT_RESTART_REQUESTED",
    "COMPONENT_RESTART_SUCCEEDED",
    "COMPONENT_RESTART_FAILED",
    "STORAGE_POOL_STATS",
    "STORAGE_QUERY",
    "STORAGE_ERROR_CLASS",
    "STORAGE_SLOW_QUERY",
    "STORAGE_MIGRATION",
    "AGENT_BOUND",
    "AGENT_SWITCHED",
    "CLIENT_ATTACH",
    "CLIENT_DETACHED",
    "CLIENT_EXIT_CLEAN",
    "SESSION_CLOSED",
    "SESSION_COMPACTION_ARCHIVE",
    "SESSION_CONTEXT_BUDGET",
    "SESSION_CREATED",
    "SESSION_EXPIRED",
    "SESSION_RESUMED",
    "SESSION_STALE",
    "SESSION_STATUS_CHANGED",
    "SESSION_SUMMARY_ENRICHED",
    "RESPONSE_ACKED",
    "RESPONSE_DELIVERED",
    "RESPONSE_PERSISTED",
    "RESPONSE_SUPPRESSED",
    "TRAILER_EMITTED",
    "TRAILER_EXPECTED",
    "TRAILER_FEEDBACK_PENDING",
    "LLM_CACHE_METRICS",
    "LLM_CALL_COMPLETED",
    "LLM_CALL_STARTED",
    "LLM_REQUEST_STARTED",
    "LLM_CALL_LEGACY",
    "MEMORY_CANDIDATE_DELETE",
    "MEMORY_CANDIDATE_PROMOTE",
    "MEMORY_CANDIDATE_PUT",
    "MEMORY_CANDIDATE_UPDATE",
    "MEMORY_CAPSULE_REFRESHED",
    "MEMORY_CAPSULE_REFRESH_FAILED",
    "MEMORY_CAPSULE_REFRESH_SKIPPED",
    "MEMORY_CONTEXT_BUILT",
    "MEMORY_CONTEXT_FAILED",
    "MEMORY_FOLLOWUP_COMPLETED",
    "MEMORY_FOLLOWUP_FAILED",
    "MEMORY_FOLLOWUP_PENDING",
    "MEMORY_POLICY_SNAPSHOT",
    "MEMORY_RECORD_DELETE",
    "MEMORY_RECORD_FEEDBACK",
    "MEMORY_RECORD_INVALIDATE",
    "MEMORY_RECORD_PUT",
    "MEMORY_RECORD_SUPERSEDE",
    "MEMORY_RECORD_TOMBSTONE",
    "MEMORY_RECORD_UPSERT",
    "MEMORY_RELATION_PUT",
    "MEMORY_RETRIEVAL_BUILT",
    "MEMORY_TIER_TRANSITION_PUT",
    "MEMORY_TURN_RECORDED",
    "MEMORY_TURN_RECORD_FAILED",
    "MEMORY_WRITE_COMPLETED",
    "MEMORY_WRITE_FAILED",
    "MEMORY_WRITE_STARTED",
    "MEMORY_PROMOTION_EVALUATED",
    "MEMORY_TIER_TRANSITION_APPLIED",
    "MEMORY_OUTCOME_FEEDBACK_APPLIED",
    "MEMORY_RETRIEVAL_POLICY_DECIDED",
    "MEMORY_RETRIEVAL_CAPPED",
    "MEMORY_GC_COMPLETED",
    "MEMORY_CONFIDENCE_DECAYED",
    "MEMORY_SCOPE_CAPACITY_EVICTED",
    "MEMORY_SOFT_DELETED_PURGED",
    "MEMORY_SUMMARY_COMPRESSED",
    "BRAIN_TUNING_ADJUSTED",
    "TASK_PLAN_ABANDONED",
    "TASK_PLAN_COMPLETED",
    "TASK_PLAN_DECLARED",
    "TASK_PLAN_INVALID_TRAILER",
    "TASK_PLAN_REVISED",
    "TASK_PLAN_STEP_BLOCKED",
    "TASK_PLAN_STEP_COMPLETED",
    "THREAD_DECISION",
    "WM_UPDATED",
    "BRAIN_GOAL_RISK_RESOLVED",
    "BRAIN_GOAL_STAGED",
    "BRAIN_GOAL_REVISION_STAGED",
    "BRAIN_META_RULE_STAGED",
    "BRAIN_SAFETY_PREEMPTED",
    "BRAIN_STRATEGY_BUDGET_CONSUMED",
    "BRAIN_FAILURE_PATTERN_OBSERVED",
    "BRAIN_LEARNING_ATTRIBUTED",
    "BRAIN_KNOWLEDGE_CONSOLIDATED",
    "BRAIN_LLM_OVERRIDE_REJECTED",
    "GOAL_CRON_ADVANCED",
    "GOAL_RESUME_CONTEXT_LOADED",
    "MRDD_REGROUNDING_INJECT",
    "MRDD_DRIFT_SIGNAL",
    "MRDD_HOOK_ERROR",
    "RVRH_RECALL_DECISION",
    "PROMPT_CACHE_OBSERVATION",
    "MSPO_MEMORY_SPAN_READ",
    "CHAT_PHASE_TIMING",
    "SFRX_SESSION_FORK",
    "SFRX_FILE_RESTORE",
    "TOOL_RUN",
    "TOOL_CALL_LEGACY",
    "TOOL_ENVELOPE_REPAIR_RETRY",
    "TOOL_ENVELOPE_REPAIR_EXHAUSTED",
    "PAE_IDLE_TICK_CANCELLED",
    "PAE_IDLE_TICK_SCHEDULED",
    "PAE_IDLE_TICK_SUPPRESSED",
    "RLM_TICK_COMPLETED",
    "RLM_TICK_STARTED",
    "TICK",
    "CRON_ANNOUNCE",
    "RUN_CANCEL_REQUESTED",
    "AUTH_DENIED",
    "APPROVAL_REQUIRED",
    "POLICY_DENIED",
    "SECRET_REDACTED",
    "SECURITY_WARNING",
    "CONTEXT_MANIFEST",
    "CONTEXT_MANIFEST_CREATED",
    "CONTEXT_PACK",
    "METRIC",
    "MESSAGE",
    "MODULE_DEBUG_FAILURE",
    "MODULE_STATS",
    "SUMMARY_UPDATED",
]
