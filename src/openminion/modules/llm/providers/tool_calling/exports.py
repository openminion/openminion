"""Lazy export table for provider tool-calling helpers."""

from __future__ import annotations

from importlib import import_module
from typing import Any

PUBLIC_EXPORTS = [
    'ERROR_INVALID_TOOL_ARGUMENTS',
    'ERROR_UNKNOWN_TOOL_NAME',
    'ERROR_UNPARSEABLE_TOOL_ENVELOPE',
    'NormalizedToolCallResult',
    'PARSE_ERRORS_KEY',
    'PARSE_FORMAT_KEY',
    'PARSE_STRATEGY_KEY',
    'ToolCallNormalizer',
    'ToolCallParseError',
    'ToolCallParseResult',
    'normalize_tool_calls',
    'build_fallback_tool_call_instruction',
    'build_openai_tools_payload',
    'build_tool_schema_name_map',
    'detect_raw_envelope',
    'detect_raw_tool_payload_json',
    'detect_raw_tool_markup',
    'detect_raw_xml_tool_wrapper',
    'extract_fallback_tool_calls_from_text',
    'extract_fallback_tool_calls_from_text_with_metadata',
    'extract_openai_message_tool_calls',
    'is_schema_only_submit_output_tools',
    'normalize_tool_call_strategy',
    'normalize_tool_choice',
    'resolve_tool_call_source_precedence',
    '_CHANNEL_ENVELOPE_MALFORMED_RE',
    '_CHANNEL_ENVELOPE_RE',
    '_extract_channel_envelope_calls',
    '_resolve_allowed_tool_name',
    'remap_provider_tool_call_name',
    'resolve_tool_schema_capability',
    'sanitize_envelope_leak',
    'supports_fallback_tool_calling',
    'supports_native_tool_calling',
    'ToolSchemaCapability',
    'ToolCallFallbackSource',
    'ToolCallSourceResolution',
    'ToolSchemaNameMap',
    'ProviderToolCall',
    'ProviderToolSpec',
]

LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    'ERROR_INVALID_TOOL_ARGUMENTS': ('.base', 'ERROR_INVALID_TOOL_ARGUMENTS'),
    'ERROR_UNKNOWN_TOOL_NAME': ('.base', 'ERROR_UNKNOWN_TOOL_NAME'),
    'ERROR_UNPARSEABLE_TOOL_ENVELOPE': ('.base', 'ERROR_UNPARSEABLE_TOOL_ENVELOPE'),
    'NormalizedToolCallResult': ('.normalizer', 'NormalizedToolCallResult'),
    'PARSE_ERRORS_KEY': ('.normalizer', 'PARSE_ERRORS_KEY'),
    'PARSE_FORMAT_KEY': ('.normalizer', 'PARSE_FORMAT_KEY'),
    'PARSE_STRATEGY_KEY': ('.normalizer', 'PARSE_STRATEGY_KEY'),
    'ToolCallNormalizer': ('.normalizer', 'ToolCallNormalizer'),
    'ToolCallParseError': ('.base', 'ToolCallParseError'),
    'ToolCallParseResult': ('.base', 'ToolCallParseResult'),
    'normalize_tool_calls': ('.normalizer', 'normalize_tool_calls'),
    'build_fallback_tool_call_instruction': ('.contracts', 'build_fallback_tool_call_instruction'),
    'build_openai_tools_payload': ('.contracts', 'build_openai_tools_payload'),
    'build_tool_schema_name_map': ('.capabilities', 'build_tool_schema_name_map'),
    'detect_raw_envelope': ('.contracts', 'detect_raw_envelope'),
    'detect_raw_tool_payload_json': ('.contracts', 'detect_raw_tool_payload_json'),
    'detect_raw_tool_markup': ('.contracts', 'detect_raw_tool_markup'),
    'detect_raw_xml_tool_wrapper': ('.contracts', 'detect_raw_xml_tool_wrapper'),
    'extract_fallback_tool_calls_from_text': ('.facade', 'extract_fallback_tool_calls_from_text'),
    'extract_fallback_tool_calls_from_text_with_metadata': ('.facade', 'extract_fallback_tool_calls_from_text_with_metadata'),
    'extract_openai_message_tool_calls': ('.facade', 'extract_openai_message_tool_calls'),
    'is_schema_only_submit_output_tools': ('.contracts', 'is_schema_only_submit_output_tools'),
    'normalize_tool_call_strategy': ('.contracts', 'normalize_tool_call_strategy'),
    'normalize_tool_choice': ('.contracts', 'normalize_tool_choice'),
    'resolve_tool_call_source_precedence': ('.source_precedence', 'resolve_tool_call_source_precedence'),
    '_CHANNEL_ENVELOPE_MALFORMED_RE': ('.contracts', '_CHANNEL_ENVELOPE_MALFORMED_RE'),
    '_CHANNEL_ENVELOPE_RE': ('.contracts', '_CHANNEL_ENVELOPE_RE'),
    '_extract_channel_envelope_calls': ('.facade', '_extract_channel_envelope_calls'),
    '_resolve_allowed_tool_name': ('.contracts', '_resolve_allowed_tool_name'),
    'remap_provider_tool_call_name': ('.capabilities', 'remap_provider_tool_call_name'),
    'resolve_tool_schema_capability': ('.capabilities', 'resolve_tool_schema_capability'),
    'sanitize_envelope_leak': ('.contracts', 'sanitize_envelope_leak'),
    'supports_fallback_tool_calling': ('.contracts', 'supports_fallback_tool_calling'),
    'supports_native_tool_calling': ('.contracts', 'supports_native_tool_calling'),
    'ToolSchemaCapability': ('.capabilities', 'ToolSchemaCapability'),
    'ToolCallFallbackSource': ('.source_precedence', 'ToolCallFallbackSource'),
    'ToolCallSourceResolution': ('.source_precedence', 'ToolCallSourceResolution'),
    'ToolSchemaNameMap': ('.capabilities', 'ToolSchemaNameMap'),
    'ProviderToolCall': ('openminion.modules.llm.providers.base', 'ProviderToolCall'),
    'ProviderToolSpec': ('openminion.modules.llm.providers.base', 'ProviderToolSpec'),
}


def resolve_lazy_export(*, package_name: str, name: str) -> Any:
    target = LAZY_EXPORTS.get(name)
    if not target:
        raise AttributeError(name)
    module_name, attr_name = target
    module = import_module(module_name, package=package_name)
    return getattr(module, attr_name)
