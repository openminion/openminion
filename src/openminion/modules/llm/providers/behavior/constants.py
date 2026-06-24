"""Fixed internal constants for provider behavior profiles."""

MINIMAX_OPENAI_DIALECT_ENDPOINT_MARKERS: tuple[str, ...] = (
    "api.minimax.io",
    "dashscope.aliyuncs.com",
)


DEFAULT_REQUEST_DIALECT: str = "openai_default"
MINIMAX_OPENAI_COMPAT_REQUEST_DIALECT: str = "minimax_openai_compat"


DEFAULT_TOOL_CHOICE_POLICY: str = "default"
MINIMAX_OPENAI_COMPAT_TOOL_CHOICE_POLICY: str = "minimax_openai_compat"


DEFAULT_FALLBACK_PARSER_POLICY: str = "full"
STRUCTURED_FALLBACK_PARSER_POLICY: str = "structured"


OPENAI_CHAT_TRANSPORT_ADAPTER: str = "openai_chat"
OPENAI_CHAT_COMPLETIONS_WIRE_PROTOCOL_FAMILY: str = "openai_chat_completions"
OPENAI_SERVICE_VENDOR: str = "openai"
MINIMAX_SERVICE_VENDOR: str = "minimax"
DASHSCOPE_SERVICE_VENDOR: str = "dashscope"
OPENAI_MODEL_FAMILY: str = "openai"
GPT_MODEL_FAMILY: str = "gpt"
MINIMAX_MODEL_FAMILY: str = "minimax"
QWEN_MODEL_FAMILY: str = "qwen"
GLM_MODEL_FAMILY: str = "glm"
KIMI_MODEL_FAMILY: str = "kimi"
CLAUDE_MODEL_FAMILY: str = "claude"
