SELF_COMPACTION_EVENT_TYPE = "context.self_compaction"
SELF_COMPACTION_MAX_CHARS = 800
BUDGET_FINALIZATION_STATUS_RETRY_PROMPT = (
    "You already produced the user-facing final answer above. Do not repeat or "
    "expand it. Return only the structured finalization_status signal now. Do "
    "not call tools. Set status=final_answer only if that prior answer fully "
    "completed the request. Use status=incomplete or status=blocked otherwise."
)
BUDGET_ANSWER_ONLY_COLLECTION_ITEM_LIMIT = 8
BUDGET_ANSWER_ONLY_NESTED_TEXT_LIMIT = 300
BUDGET_ANSWER_ONLY_STRING_TEXT_LIMIT = 600
BUDGET_ANSWER_ONLY_TOOL_RESULT_LIMIT = 8
BUDGET_ANSWER_ONLY_TOOL_NAME_LIMIT = 80
BUDGET_ANSWER_ONLY_TEXT_LIMIT = 1200
CIRCULAR_TOOL_SEQUENCE_LIMIT = 3
MUTATING_FILE_REPEAT_CLOSEOUT_LIMIT = 3

PROVIDER_RETRYABLE_CATEGORIES: frozenset[str] = frozenset(
    {"RATE_LIMITED", "TIMEOUT", "PROVIDER_ERROR"}
)
PROVIDER_RETRY_DEFAULT_MAX_ATTEMPTS = 3
PROVIDER_RETRY_DEFAULT_BASE_BACKOFF_MS = 250.0
PROVIDER_RETRY_DEFAULT_MAX_BACKOFF_MS = 4000.0
PROVIDER_RETRY_DEFAULT_JITTER_RATIO = 0.25

PLAN_TOOL_LAST_SUBSTANTIVE_COUNT_SCRATCHPAD_KEY = (
    "plan_tool.last_substantive_result_count"
)
