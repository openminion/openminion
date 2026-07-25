"""Lazy export table for the LLM providers package."""

from __future__ import annotations

from typing import Any

PUBLIC_EXPORTS = [
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

LAZY_EXPORTS: dict[str, tuple[str, str]] = {
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
    "build_openai_tools_payload": (".tool_calling", "build_openai_tools_payload"),
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


def resolve_lazy_export(*, package_name: str, name: str) -> Any:
    target = LAZY_EXPORTS.get(name)
    if not target:
        raise AttributeError(name)
    module_name, attr_name = target
    module = __import__(package_name + module_name, fromlist=[attr_name])
    return getattr(module, attr_name)
