"""Model capability profiles used by brain LLM adapters."""

from dataclasses import dataclass, fields


class DecisionStrategy:
    FULL_SCHEMA = "full_schema"
    SIMPLIFIED_SCHEMA = "simplified_schema"
    TWO_STEP_CLASSIFY = "two_step_classify"


class RetryStrategy:
    SAME_SCHEMA = "same_schema"
    PROGRESSIVE_SIMPLIFICATION = "progressive_simplification"


@dataclass(frozen=True)
class ModelCapabilityProfile:
    profile_id: str
    model_fragments: tuple[str, ...]
    decision_strategy: str = DecisionStrategy.FULL_SCHEMA
    extraction_chain: tuple[str, ...] = ("tool_calls", "json_body")
    retry_strategy: str = RetryStrategy.SAME_SCHEMA
    max_structured_retries: int = 3
    retry_nudge_style: str = ""


_PROFILE_FIELD_NAMES = {field.name for field in fields(ModelCapabilityProfile)}
