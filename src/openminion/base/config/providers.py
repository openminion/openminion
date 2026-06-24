"""Provider config dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OpenAIProviderConfig:
    model: str = "gpt-4.1-mini"
    api_key: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: int = 60
    temperature: float = 0.2
    tool_call_strategy: str = "hybrid"


@dataclass
class AnthropicProviderConfig:
    model: str = "claude-3-5-sonnet-latest"
    api_key: str = ""
    api_key_env: str = "ANTHROPIC_API_KEY"
    base_url: str = "https://api.anthropic.com/v1"
    timeout_seconds: int = 60
    temperature: float = 0.2
    max_tokens: int = 1024
    api_version: str = "2023-06-01"


@dataclass
class OpenRouterProviderConfig:
    model: str = "openai/gpt-4.1-mini"
    api_key: str = ""
    api_key_env: str = "OPENROUTER_API_KEY"
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: int = 60
    temperature: float = 0.2
    app_name: str = "openminion"
    app_url: str = ""
    tool_call_strategy: str = "hybrid"
    max_tokens: int = 2048


@dataclass
class CerebrasProviderConfig:
    model: str = "gpt-oss-120b"
    api_key: str = ""
    api_key_env: str = "CEREBRAS_API_KEY"
    base_url: str = "https://api.cerebras.ai/v1"
    timeout_seconds: int = 120
    temperature: float = 0.2
    tool_call_strategy: str = "hybrid"


@dataclass
class GroqProviderConfig:
    model: str = "llama-3.3-70b-versatile"
    api_key: str = ""
    api_key_env: str = "GROQ_API_KEY"
    base_url: str = "https://api.groq.com/openai/v1"
    timeout_seconds: int = 120
    temperature: float = 0.2
    tool_call_strategy: str = "hybrid"


@dataclass
class OllamaProviderConfig:
    model: str = "llama3.1"
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: int = 60
    temperature: float = 0.2
    api_key: str = ""
    api_key_env: str = "OLLAMA_API_KEY"
    tool_call_strategy: str = "fallback"


@dataclass
class CortensorProviderConfig:
    model: str = "gpt-oss-20b"
    base_url: str = "http://127.0.0.1:8080/api/v2/completions"
    timeout_seconds: int = 420
    transport_timeout_buffer_seconds: int = 30
    result_wait_attempts: int = 3
    result_wait_interval_seconds: float = 2.0
    precommit_timeout_seconds: int = 300
    max_tokens: int = 4096
    temperature: float = 0.2
    top_p: float = 1.0
    top_k: int = 0
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0
    node_type: int = 0
    api_key: str = ""
    api_key_env: str = "CORTENSOR_API_KEY"
    tool_call_strategy: str = "hybrid"
    api_mode: str = "auto"
    session_id: int = 1
    session_ids: list[int] = field(default_factory=list)
    session_pool: str = "auto"
    dedicated_session_ids: list[int] = field(default_factory=list)
    ephemeral_session_ids: list[int] = field(default_factory=list)
    session_parallel_requests: int = 1
    session_retry_rounds: int = 1
    prompt_type: int = 1
    stream: bool = False
    privacy_level: str = "high"


@dataclass
class ProvidersConfig:
    openai: OpenAIProviderConfig = field(default_factory=OpenAIProviderConfig)
    anthropic: AnthropicProviderConfig = field(default_factory=AnthropicProviderConfig)
    openrouter: OpenRouterProviderConfig = field(
        default_factory=OpenRouterProviderConfig
    )
    cerebras: CerebrasProviderConfig = field(default_factory=CerebrasProviderConfig)
    groq: GroqProviderConfig = field(default_factory=GroqProviderConfig)
    ollama: OllamaProviderConfig = field(default_factory=OllamaProviderConfig)
    cortensor: CortensorProviderConfig = field(default_factory=CortensorProviderConfig)


__all__ = [
    "AnthropicProviderConfig",
    "CerebrasProviderConfig",
    "CortensorProviderConfig",
    "GroqProviderConfig",
    "OllamaProviderConfig",
    "OpenAIProviderConfig",
    "OpenRouterProviderConfig",
    "ProvidersConfig",
]
