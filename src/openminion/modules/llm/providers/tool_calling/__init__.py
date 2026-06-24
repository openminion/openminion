# ruff: noqa: F401

from typing import Any, Iterable

from openminion.modules.llm.providers.base import ProviderToolCall, ProviderToolSpec
from openminion.modules.llm.providers.tool_calling.base import (
    ERROR_INVALID_TOOL_ARGUMENTS,
    ERROR_UNKNOWN_TOOL_NAME,
    ERROR_UNPARSEABLE_TOOL_ENVELOPE,
    ToolCallParseError,
    ToolCallParseResult,
)
from openminion.modules.llm.providers.tool_calling.normalizer import (
    NormalizedToolCallResult,
    PARSE_ERRORS_KEY,
    PARSE_FORMAT_KEY,
    PARSE_STRATEGY_KEY,
    ToolCallNormalizer,
    normalize_tool_calls,
)
from openminion.modules.llm.providers.tool_calling.capabilities import (
    ToolSchemaCapability,
    ToolSchemaNameMap,
    build_tool_schema_name_map,
    remap_provider_tool_call_name,
    resolve_tool_schema_capability,
)
from openminion.modules.llm.providers.tool_calling.registry import (
    parse_fallback_tool_calls,
    parse_native_tool_calls,
)
from openminion.modules.llm.providers.tool_calling.source_precedence import (
    ToolCallFallbackSource,
    ToolCallSourceResolution,
    resolve_tool_call_source_precedence,
)
from openminion.modules.llm.providers.tool_calling.contracts import (
    _CHANNEL_ENVELOPE_MALFORMED_RE,
    _CHANNEL_ENVELOPE_RE,
    build_fallback_tool_call_instruction,
    build_openai_tools_payload,
    detect_raw_envelope,
    detect_raw_tool_payload_json,
    detect_raw_tool_markup,
    detect_raw_xml_tool_wrapper,
    is_schema_only_submit_output_tools,
    normalize_tool_call_strategy,
    normalize_tool_choice,
    _resolve_allowed_tool_name,
    sanitize_envelope_leak,
    supports_fallback_tool_calling,
    supports_native_tool_calling,
)

__all__ = [
    "ERROR_INVALID_TOOL_ARGUMENTS",
    "ERROR_UNKNOWN_TOOL_NAME",
    "ERROR_UNPARSEABLE_TOOL_ENVELOPE",
    "NormalizedToolCallResult",
    "PARSE_ERRORS_KEY",
    "PARSE_FORMAT_KEY",
    "PARSE_STRATEGY_KEY",
    "ToolCallNormalizer",
    "ToolCallParseError",
    "ToolCallParseResult",
    "normalize_tool_calls",
    "build_fallback_tool_call_instruction",
    "build_openai_tools_payload",
    "build_tool_schema_name_map",
    "detect_raw_envelope",
    "detect_raw_tool_payload_json",
    "detect_raw_tool_markup",
    "detect_raw_xml_tool_wrapper",
    "extract_fallback_tool_calls_from_text",
    "extract_fallback_tool_calls_from_text_with_metadata",
    "extract_openai_message_tool_calls",
    "is_schema_only_submit_output_tools",
    "normalize_tool_call_strategy",
    "normalize_tool_choice",
    "resolve_tool_call_source_precedence",
    "_CHANNEL_ENVELOPE_MALFORMED_RE",
    "_CHANNEL_ENVELOPE_RE",
    "_extract_channel_envelope_calls",
    "_resolve_allowed_tool_name",
    "remap_provider_tool_call_name",
    "resolve_tool_schema_capability",
    "sanitize_envelope_leak",
    "supports_fallback_tool_calling",
    "supports_native_tool_calling",
    "ToolSchemaCapability",
    "ToolCallFallbackSource",
    "ToolCallSourceResolution",
    "ToolSchemaNameMap",
    "ProviderToolCall",
    "ProviderToolSpec",
]


def extract_openai_message_tool_calls(
    message_payload: Any,
    *,
    provider_name: str | None = None,
    model_name: str | None = None,
    allowed_tool_names: Iterable[str] | None = None,
) -> list[ProviderToolCall]:
    result = parse_native_tool_calls(
        message_payload,
        provider_name=provider_name,
        model_name=model_name,
        allowed_tool_names=list(allowed_tool_names or []),
    )
    return result.calls


def extract_fallback_tool_calls_from_text(
    text: str,
    *,
    provider_name: str | None = None,
    model_name: str | None = None,
    allowed_tool_names: Iterable[str] | None = None,
) -> list[ProviderToolCall]:
    result = parse_fallback_tool_calls(
        text,
        provider_name=provider_name,
        model_name=model_name,
        allowed_tool_names=allowed_tool_names,
    )
    return result.calls


def extract_fallback_tool_calls_from_text_with_metadata(
    text: str,
    *,
    provider_name: str | None = None,
    model_name: str | None = None,
    allowed_tool_names: Iterable[str] | None = None,
) -> tuple[list[ProviderToolCall], dict[str, Any]]:
    result = parse_fallback_tool_calls(
        text,
        provider_name=provider_name,
        model_name=model_name,
        allowed_tool_names=allowed_tool_names,
    )
    return result.calls, dict(result.metadata or {})


def _extract_channel_envelope_calls(
    text: str,
    *,
    allowed_tool_names: Iterable[str] | None = None,
) -> list[ProviderToolCall]:
    result = parse_fallback_tool_calls(
        text,
        provider_name="openrouter",
        model_name="",
        allowed_tool_names=allowed_tool_names,
    )
    return result.calls
