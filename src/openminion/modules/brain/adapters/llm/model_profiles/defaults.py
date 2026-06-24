from .capabilities import (
    DecisionStrategy,
    ModelCapabilityProfile,
    RetryStrategy,
    _PROFILE_FIELD_NAMES,
)

claude_default = ModelCapabilityProfile(
    profile_id="claude_default",
    model_fragments=("claude",),
)

gpt5_default = ModelCapabilityProfile(
    profile_id="gpt5_default",
    model_fragments=("gpt-5",),
    retry_strategy=RetryStrategy.PROGRESSIVE_SIMPLIFICATION,
    retry_nudge_style="openai_function_calling",
)

gpt4_default = ModelCapabilityProfile(
    profile_id="gpt4_default",
    model_fragments=("gpt-4", "gpt-4o", "gpt-4.1"),
    retry_strategy=RetryStrategy.PROGRESSIVE_SIMPLIFICATION,
    retry_nudge_style="openai_function_calling",
)

glm_default = ModelCapabilityProfile(
    profile_id="glm_default",
    model_fragments=("glm",),
    decision_strategy=DecisionStrategy.TWO_STEP_CLASSIFY,
    retry_strategy=RetryStrategy.PROGRESSIVE_SIMPLIFICATION,
    retry_nudge_style="json_body_first",
)

qwen_default = ModelCapabilityProfile(
    profile_id="qwen_default",
    model_fragments=("qwen",),
    decision_strategy=DecisionStrategy.TWO_STEP_CLASSIFY,
    retry_strategy=RetryStrategy.PROGRESSIVE_SIMPLIFICATION,
    retry_nudge_style="openai_function_calling",
)

minimax_default = ModelCapabilityProfile(
    profile_id="minimax_default",
    model_fragments=("minimax",),
    decision_strategy=DecisionStrategy.TWO_STEP_CLASSIFY,
    retry_strategy=RetryStrategy.PROGRESSIVE_SIMPLIFICATION,
    retry_nudge_style="json_body_first",
)

kimi_default = ModelCapabilityProfile(
    profile_id="kimi_default",
    model_fragments=("kimi",),
    decision_strategy=DecisionStrategy.TWO_STEP_CLASSIFY,
    retry_strategy=RetryStrategy.PROGRESSIVE_SIMPLIFICATION,
    retry_nudge_style="openai_function_calling",
)

llama_default = ModelCapabilityProfile(
    profile_id="llama_default",
    model_fragments=("llama", "llama3"),
    decision_strategy=DecisionStrategy.SIMPLIFIED_SCHEMA,
    extraction_chain=("json_body", "tool_calls"),
    retry_strategy=RetryStrategy.PROGRESSIVE_SIMPLIFICATION,
)

ollama_default = ModelCapabilityProfile(
    profile_id="ollama_default",
    model_fragments=("ollama",),
    decision_strategy=DecisionStrategy.SIMPLIFIED_SCHEMA,
    extraction_chain=("json_body",),
    retry_strategy=RetryStrategy.PROGRESSIVE_SIMPLIFICATION,
    max_structured_retries=2,
)

fallback = ModelCapabilityProfile(
    profile_id="fallback",
    model_fragments=(),
)

_DEFAULT_PROFILES: tuple[ModelCapabilityProfile, ...] = (
    claude_default,
    gpt5_default,
    gpt4_default,
    qwen_default,
    glm_default,
    minimax_default,
    kimi_default,
    llama_default,
    ollama_default,
    fallback,
)

__all__ = [
    "_DEFAULT_PROFILES",
    "_PROFILE_FIELD_NAMES",
    "claude_default",
    "fallback",
    "glm_default",
    "gpt4_default",
    "gpt5_default",
    "kimi_default",
    "llama_default",
    "minimax_default",
    "ollama_default",
    "qwen_default",
]
