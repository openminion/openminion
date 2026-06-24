from typing import Any, Iterable

from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.llm.providers.tool_calling.base import ToolCallParseResult
from openminion.modules.llm.providers.tool_calling.contracts import (
    _coerce_tool_arguments,
    _coerce_tool_arguments_for_resolved_tool,
    _normalize_allowed_tool_names,
    _resolve_allowed_tool_name,
)


class OpenAINativeToolCallParser:
    name = "openai_native"

    def parse_native(
        self,
        message_payload: Any,
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> ToolCallParseResult:
        if not isinstance(message_payload, dict):
            return ToolCallParseResult()

        allowed = _normalize_allowed_tool_names(allowed_tool_names)
        metadata = {"tool_parse_format": "openai_native"}
        parsed_calls: list[ProviderToolCall] = []
        raw_tool_calls = message_payload.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            for item in raw_tool_calls:
                if not isinstance(item, dict):
                    continue
                function_payload = item.get("function")
                if not isinstance(function_payload, dict):
                    continue

                name = str(function_payload.get("name", "")).strip()
                arguments = _coerce_tool_arguments(function_payload.get("arguments"))
                resolved_name = _resolve_allowed_tool_name(
                    name,
                    allowed_tool_names=allowed,
                    arguments=arguments,
                )
                if not resolved_name:
                    continue

                parsed_calls.append(
                    ProviderToolCall(
                        id=str(item.get("id", "")).strip(),
                        name=resolved_name,
                        arguments=_coerce_tool_arguments_for_resolved_tool(
                            resolved_name,
                            arguments,
                        ),
                        source="native",
                    )
                )

        if not parsed_calls:
            function_call = message_payload.get("function_call")
            if not isinstance(function_call, dict):
                return ToolCallParseResult()
            name = str(function_call.get("name", "")).strip()
            arguments = _coerce_tool_arguments(function_call.get("arguments"))
            resolved_name = _resolve_allowed_tool_name(
                name,
                allowed_tool_names=allowed,
                arguments=arguments,
            )
            if not resolved_name:
                return ToolCallParseResult()
            parsed_calls.append(
                ProviderToolCall(
                    id="",
                    name=resolved_name,
                    arguments=_coerce_tool_arguments_for_resolved_tool(
                        resolved_name,
                        arguments,
                    ),
                    source="native",
                )
            )
        return ToolCallParseResult(calls=parsed_calls, metadata=metadata)

    def parse_text(
        self,
        text: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> ToolCallParseResult:
        del text, allowed_tool_names
        return ToolCallParseResult()
