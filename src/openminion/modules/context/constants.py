from openminion.modules.paths import (
    IDENTITY_DB_SUBPATH,
    SESSION_DB_SUBPATH,
    STANDALONE_SESSION_DB_SUBPATH,
)

from openminion.modules.task.constants import (  # noqa: F401
    TASK_PLAN_OUTPUT_SUMMARY_MAX_CHARS,
    TASK_PLAN_TOOL_FAMILIES,
)

OPENMINION_STRICT_CONTEXT_CONTRACTS_ENV = "OPENMINION_STRICT_CONTEXT_CONTRACTS"

ARTIFACT_PREVIEW_MAX_CHARS = 300
ARTIFACT_PREVIEW_MAX_BULLETS = 3
ARTIFACT_PER_ITEM_MAX_TOKENS = 120
ACTIVE_STATE_MAX_CHARS = 2000
RECENT_TURN_ASSISTANT_MAX_TOKENS = 300
TRAILER_FEEDBACK_MAX_CHARS = 800  # ~200 tokens at 4:1 heuristic
CONTEXT_PURPOSE_DECIDE = "decide"
CONTEXT_BUCKET_RECENT_WINDOW = "recent_window"
TRIM_ORDER: list[str] = [
    "evidence_refs",
    "retrieval",
    CONTEXT_BUCKET_RECENT_WINDOW,
    "summaries",
]
PINNED_BUCKETS: frozenset[str] = frozenset(
    {
        "static_prefix",
        "mission_snapshot",
        "task_digest",
        "conversation_summary",
        "active_plan",
        "trailer_feedback",
        "turn_input",
    }
)
BLOCK_PRIORITY: dict[str, str] = {
    "safety": "P0",
    "identity": "P0",
    "task_header": "P1",
    "instructions": "P1",
    "dialogue": "P1",
    "summary": "P2",
    "continuation": "P0",
    "active_state": "P2",
    "retrieval": "P2",
    "skills": "P2",
    "facts": "P3",
    "memory": "P3",
    "tool_events": "P3",
    "artifacts": "P4",
}
CONTEXT_BUDGET_TIER_SHORT = "short"
CONTEXT_BUDGET_TIER_MEDIUM = "medium"
CONTEXT_BUDGET_TIER_FULL = "full"
ACTIVE_STATE_CLARIFY_DIGEST_MAX_QUESTIONS = 3
ACTIVE_STATE_CLARIFY_DIGEST_MAX_ANSWERS = 3
ACTIVE_STATE_CLARIFY_DIGEST_MAX_CHARS = 640
ACTIVE_STATE_MAX_INTENT_ITEMS = 5
DECISION_MEMORY_LIMIT = 5
DECISION_RATIONALE_RENDER_MAX_CHARS = 160
CONTEXT_DROP_VISIBILITY_BUCKETS: tuple[str, ...] = (
    "retrieval",
    "evidence_refs",
    CONTEXT_BUCKET_RECENT_WINDOW,
)
CONTEXT_DROP_VISIBILITY_BUCKET_LABELS: dict[str, str] = {
    "retrieval": "retrieval",
    "evidence_refs": "evidence",
    CONTEXT_BUCKET_RECENT_WINDOW: "recent window",
}
CONTEXT_DROP_VISIBILITY_NOTE_MAX_CHARS = 360
CONTEXT_DECISION_TRACE_VERSION = "context-decision.v1"
CONTEXT_DECISION_TRACE_MAX_REFERENCES = 512
CONTEXT_DECISION_TRACE_MAX_BYTES = 64 * 1024

COMPACTION_REASON_OK = "OK"
COMPACTION_REASON_BELOW_THRESHOLD = "BELOW_THRESHOLD"
COMPACTION_REASON_ALREADY_COMPACTED_THIS_TURN = "ALREADY_COMPACTED_THIS_TURN"
COMPACTION_REASON_CONSOLIDATION_NOT_YET_RUN = "CONSOLIDATION_NOT_YET_RUN"

# PIDF input-boundary process-local audit ledger bound.
INPUT_BOUNDARY_LEDGER_MAX_EVENTS = 1024
PRIOR_TURN_CONTEXT_CHAR_LIMIT = 300

# Path Layout
DEFAULT_STANDALONE_SESSION_DB_SUBPATH = STANDALONE_SESSION_DB_SUBPATH
DEFAULT_INTEGRATED_SESSION_DB_SUBPATH = SESSION_DB_SUBPATH
DEFAULT_INTEGRATED_IDENTITY_DB_SUBPATH = IDENTITY_DB_SUBPATH
