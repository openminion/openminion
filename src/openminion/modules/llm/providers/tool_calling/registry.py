from typing import Any
from collections.abc import Iterable, Sequence

from openminion.modules.llm.providers.tool_calling.base import ToolCallParseResult
from openminion.modules.llm.providers.tool_calling.models import (
    JsonFallbackToolCallParser,
    MinimaxBracketToolCallParser,
    MinimaxXmlToolCallParser,
    OpenAINativeToolCallParser,
    PlainCliToolCommandParser,
    PlainToolDirectiveParser,
)
from openminion.modules.llm.providers.tool_calling.providers import (
    OpenRouterEnvelopeParser,
)

_NATIVE_HANDLERS = [OpenAINativeToolCallParser()]
_MODEL_HANDLERS = {
    "minimax": [
        MinimaxXmlToolCallParser(),
        MinimaxBracketToolCallParser(),
        PlainCliToolCommandParser(),
        PlainToolDirectiveParser(),
        JsonFallbackToolCallParser(),
    ],
    "default": [
        MinimaxXmlToolCallParser(),
        MinimaxBracketToolCallParser(),
        PlainCliToolCommandParser(),
        PlainToolDirectiveParser(),
        JsonFallbackToolCallParser(),
    ],
}
_STRUCTURED_MODEL_HANDLERS = {
    "minimax": [
        MinimaxXmlToolCallParser(),
        MinimaxBracketToolCallParser(),
        PlainCliToolCommandParser(),
        JsonFallbackToolCallParser(
            allow_explicit_tool_envelopes=False,
            name="json_schema",
        ),
    ],
    "default": [
        MinimaxXmlToolCallParser(),
        MinimaxBracketToolCallParser(),
        PlainCliToolCommandParser(),
        JsonFallbackToolCallParser(
            allow_explicit_tool_envelopes=False,
            name="json_schema",
        ),
    ],
}
_PROVIDER_ENVELOPE_HANDLERS = {
    "openrouter": [OpenRouterEnvelopeParser()],
}

_PARSER_HANDLERS_BY_NAME = {
    handler.name: handler
    for handler in (
        *_NATIVE_HANDLERS,
        *_MODEL_HANDLERS["default"],
        *_STRUCTURED_MODEL_HANDLERS["default"],
        *_PROVIDER_ENVELOPE_HANDLERS["openrouter"],
    )
}


def resolve_fallback_parser_plugins(
    *,
    provider_name: str | None = None,
    model_name: str | None = None,
    fallback_parser_policy: str = "full",
) -> tuple[str, ...]:
    plugin_names: list[str] = []
    provider = str(provider_name or "").strip().lower()
    model = str(model_name or "").strip().lower()
    if provider in _PROVIDER_ENVELOPE_HANDLERS:
        plugin_names.extend(
            handler.name for handler in _PROVIDER_ENVELOPE_HANDLERS[provider]
        )

    structured_only = str(fallback_parser_policy or "").strip().lower() == "structured"
    model_key = "minimax" if "minimax" in model else "default"
    handlers = (
        _STRUCTURED_MODEL_HANDLERS.get(model_key, _STRUCTURED_MODEL_HANDLERS["default"])
        if structured_only
        else _MODEL_HANDLERS.get(model_key, _MODEL_HANDLERS["default"])
    )
    plugin_names.extend(handler.name for handler in handlers)
    return tuple(plugin_names)


def parse_native_tool_calls(
    message_payload: Any,
    *,
    provider_name: str | None = None,
    model_name: str | None = None,
    allowed_tool_names: Sequence[str] | None = None,
) -> ToolCallParseResult:
    del provider_name, model_name
    for handler in _NATIVE_HANDLERS:
        result = handler.parse_native(
            message_payload, allowed_tool_names=allowed_tool_names
        )
        if result.calls:
            return result
    return ToolCallParseResult()


def parse_fallback_tool_calls(
    text: str,
    *,
    provider_name: str | None = None,
    model_name: str | None = None,
    allowed_tool_names: Iterable[str] | None = None,
    parser_plugin_selection: Sequence[str] | None = None,
) -> ToolCallParseResult:
    collected_metadata: dict[str, Any] = {}
    parser_names = tuple(parser_plugin_selection or ())
    if parser_names:
        provider_handlers = [
            _PARSER_HANDLERS_BY_NAME[name]
            for name in parser_names
            if name in _PARSER_HANDLERS_BY_NAME
            and hasattr(_PARSER_HANDLERS_BY_NAME[name], "parse_envelope")
        ]
    else:
        provider = str(provider_name or "").strip().lower()
        provider_handlers = list(_PROVIDER_ENVELOPE_HANDLERS.get(provider, []))
        if not provider_handlers:
            # Try known envelope formats even when provider name is absent.
            provider_handlers = list(_PROVIDER_ENVELOPE_HANDLERS.get("openrouter", []))

    for handler in provider_handlers:
        result = handler.parse_envelope(text, allowed_tool_names=allowed_tool_names)
        if result.metadata:
            collected_metadata.update(result.metadata)
        if result.calls:
            if collected_metadata and result.metadata is not collected_metadata:
                result.metadata = dict(collected_metadata)
            return result

    if parser_names:
        model_handlers = [
            _PARSER_HANDLERS_BY_NAME[name]
            for name in parser_names
            if name in _PARSER_HANDLERS_BY_NAME
            and hasattr(_PARSER_HANDLERS_BY_NAME[name], "parse_text")
            and not hasattr(_PARSER_HANDLERS_BY_NAME[name], "parse_envelope")
        ]
    else:
        model_key = "default"
        model = str(model_name or "").strip().lower()
        if "minimax" in model:
            model_key = "minimax"
        model_handlers = _MODEL_HANDLERS.get(model_key, _MODEL_HANDLERS["default"])

    for handler in model_handlers:
        result = handler.parse_text(text, allowed_tool_names=allowed_tool_names)
        if result.metadata:
            collected_metadata.update(result.metadata)
        if result.calls:
            if collected_metadata and result.metadata is not collected_metadata:
                result.metadata = dict(collected_metadata)
            return result
    if collected_metadata:
        return ToolCallParseResult(metadata=collected_metadata)
    return ToolCallParseResult()


def parse_structured_tool_call_envelopes(
    text: str,
    *,
    provider_name: str | None = None,
    model_name: str | None = None,
    allowed_tool_names: Iterable[str] | None = None,
    parser_plugin_selection: Sequence[str] | None = None,
) -> ToolCallParseResult:
    del provider_name
    collected_metadata: dict[str, Any] = {}
    parser_names = tuple(parser_plugin_selection or ())
    if parser_names:
        handlers = [
            _PARSER_HANDLERS_BY_NAME[name]
            for name in parser_names
            if name in _PARSER_HANDLERS_BY_NAME
            and hasattr(_PARSER_HANDLERS_BY_NAME[name], "parse_text")
            and not hasattr(_PARSER_HANDLERS_BY_NAME[name], "parse_envelope")
        ]
    else:
        model_key = "default"
        model = str(model_name or "").strip().lower()
        if "minimax" in model:
            model_key = "minimax"
        handlers = _STRUCTURED_MODEL_HANDLERS.get(
            model_key, _STRUCTURED_MODEL_HANDLERS["default"]
        )

    for handler in handlers:
        result = handler.parse_text(text, allowed_tool_names=allowed_tool_names)
        if result.metadata:
            collected_metadata.update(result.metadata)
        if result.calls:
            if collected_metadata and result.metadata is not collected_metadata:
                result.metadata = dict(collected_metadata)
            return result
    if collected_metadata:
        return ToolCallParseResult(metadata=collected_metadata)
    return ToolCallParseResult()
