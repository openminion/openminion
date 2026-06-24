"""Fixed values for memory trust scoring and promotion rate limits."""

from .types import MemorySourceClass

DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 3600
IMPORTED_BUNDLE_RATE_LIMIT_WINDOW_SECONDS = 0
DEFAULT_USER_INPUT_MAX_PROMOTIONS: int | None = None
DEFAULT_TOOL_RESULT_MAX_PROMOTIONS = 100
DEFAULT_LLM_EXTRACTED_MAX_PROMOTIONS = 50
DEFAULT_AGENT_INFERRED_MAX_PROMOTIONS = 30
DEFAULT_IMPORTED_BUNDLE_MAX_PROMOTIONS = 200
DEFAULT_OPPOSING_PEER_HALF_LIFE_DAYS = 90.0
DURABLE_RECORD_PAGE_LIMIT = 500

DEFAULT_SOURCE_PROVENANCE: dict[MemorySourceClass, float] = {
    "user_input": 1.0,
    "tool_result": 0.8,
    "llm_extracted": 0.5,
    "agent_inferred": 0.4,
    "imported_bundle": 0.6,
}
