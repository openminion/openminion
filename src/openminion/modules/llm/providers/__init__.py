from typing import TYPE_CHECKING, Any

from .base import (
    LLMProvider,
    PROVIDER_RESPONSE_INTERFACE_VERSION,
    ProviderError,
    ProviderHistoryMessage,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderToolSpec,
    ensure_provider_response_compatibility,
    provider_response_contracts_strict,
)

__all__ = [
    "AnthropicProvider",
    "CerebrasProvider",
    "ClaudeProvider",
    "CortensorProvider",
    "EchoProvider",
    "GroqProvider",
    "LLMCTLBridgeProvider",
    "LLMProvider",
    "LocalProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "OpenRouterProvider",
    "PROVIDER_RESPONSE_INTERFACE_VERSION",
    "Provider",
    "ProviderError",
    "ProviderHistoryMessage",
    "ProviderRequest",
    "ProviderRegistry",
    "ProviderResponse",
    "ProviderToolCall",
    "ProviderToolSpec",
    "SUPPORTED_PROVIDERS",
    "StubProvider",
    "anthropic_provider",
    "build_provider",
    "build_fallback_tool_call_instruction",
    "build_openai_tools_payload",
    "cerebras_provider",
    "claude_provider",
    "cortensor_provider",
    "echo_provider",
    "extract_fallback_tool_calls_from_text",
    "extract_openai_message_tool_calls",
    "groq_provider",
    "load_plugin_providers",
    "local_provider",
    "normalize_tool_choice",
    "ollama_provider",
    "openai_provider",
    "openrouter_provider",
    "provider_response_contracts_strict",
    "register_builtin_providers",
    "stub_provider",
    "supports_fallback_tool_calling",
    "supports_native_tool_calling",
    "ensure_provider_response_compatibility",
]


if TYPE_CHECKING:  # pragma: no cover
    from .factory import SUPPORTED_PROVIDERS, build_provider
    from .bridge import LLMCTLBridgeProvider
    from .plugins import (
        ProviderRegistry,
        load_plugin_providers,
        register_builtin_providers,
    )
    from .adapters import (
        AnthropicProvider,
        CerebrasProvider,
        ClaudeProvider,
        CortensorProvider,
        EchoProvider,
        GroqProvider,
        OllamaProvider,
        OpenAIProvider,
        OpenRouterProvider,
        anthropic_provider,
        cerebras_provider,
        claude_provider,
        cortensor_provider,
        echo_provider,
        groq_provider,
        ollama_provider,
        openai_provider,
        openrouter_provider,
    )
    from .tool_calling import (
        build_fallback_tool_call_instruction,
        build_openai_tools_payload,
        extract_fallback_tool_calls_from_text,
        extract_openai_message_tool_calls,
        normalize_tool_choice,
        supports_fallback_tool_calling,
        supports_native_tool_calling,
    )
    from .contract import (
        LocalProvider,
        Provider,
        StubProvider,
        local_provider,
        stub_provider,
    )


_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "SUPPORTED_PROVIDERS": (".factory", "SUPPORTED_PROVIDERS"),
    "build_provider": (".factory", "build_provider"),
    "LLMCTLBridgeProvider": (".bridge", "LLMCTLBridgeProvider"),
    "ProviderRegistry": (".plugins", "ProviderRegistry"),
    "load_plugin_providers": (".plugins", "load_plugin_providers"),
    "register_builtin_providers": (".plugins", "register_builtin_providers"),
    "build_fallback_tool_call_instruction": (
        ".tool_calling",
        "build_fallback_tool_call_instruction",
    ),
    "build_openai_tools_payload": (
        ".tool_calling",
        "build_openai_tools_payload",
    ),
    "extract_fallback_tool_calls_from_text": (
        ".tool_calling",
        "extract_fallback_tool_calls_from_text",
    ),
    "extract_openai_message_tool_calls": (
        ".tool_calling",
        "extract_openai_message_tool_calls",
    ),
    "normalize_tool_choice": (".tool_calling", "normalize_tool_choice"),
    "supports_fallback_tool_calling": (
        ".tool_calling",
        "supports_fallback_tool_calling",
    ),
    "supports_native_tool_calling": (
        ".tool_calling",
        "supports_native_tool_calling",
    ),
    "Provider": (".contract", "Provider"),
    "LocalProvider": (".contract", "LocalProvider"),
    "StubProvider": (".contract", "StubProvider"),
    "local_provider": (".contract", "local_provider"),
    "stub_provider": (".contract", "stub_provider"),
    "AnthropicProvider": (".adapters", "AnthropicProvider"),
    "ClaudeProvider": (".adapters", "ClaudeProvider"),
    "CerebrasProvider": (".adapters", "CerebrasProvider"),
    "CortensorProvider": (".adapters", "CortensorProvider"),
    "EchoProvider": (".adapters", "EchoProvider"),
    "GroqProvider": (".adapters", "GroqProvider"),
    "OllamaProvider": (".adapters", "OllamaProvider"),
    "OpenAIProvider": (".adapters", "OpenAIProvider"),
    "OpenRouterProvider": (".adapters", "OpenRouterProvider"),
    "anthropic_provider": (".adapters", "anthropic_provider"),
    "claude_provider": (".adapters", "claude_provider"),
    "cerebras_provider": (".adapters", "cerebras_provider"),
    "cortensor_provider": (".adapters", "cortensor_provider"),
    "echo_provider": (".adapters", "echo_provider"),
    "groq_provider": (".adapters", "groq_provider"),
    "ollama_provider": (".adapters", "ollama_provider"),
    "openai_provider": (".adapters", "openai_provider"),
    "openrouter_provider": (".adapters", "openrouter_provider"),
}


def __getattr__(name: str) -> Any:  # pragma: no cover
    target = _LAZY_EXPORTS.get(name)
    if not target:
        raise AttributeError(name)
    module_name, attr_name = target
    module = __import__(__name__ + module_name, fromlist=[attr_name])
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:  # pragma: no cover
    return sorted(set(list(globals().keys()) + list(_LAZY_EXPORTS.keys())))
