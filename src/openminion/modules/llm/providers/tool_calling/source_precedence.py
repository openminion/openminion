from dataclasses import dataclass, field
from typing import Any
from collections.abc import Iterable, Sequence

from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.llm.providers.tool_calling.registry import (
    parse_fallback_tool_calls,
    parse_native_tool_calls,
    parse_structured_tool_call_envelopes,
)


@dataclass(frozen=True)
class ToolCallFallbackSource:
    source: str
    text: str


@dataclass
class ToolCallSourceResolution:
    calls: list[ProviderToolCall] = field(default_factory=list)
    selected_source: str = "none"
    attempted_fallback_sources: list[str] = field(default_factory=list)
    skipped_fallback_sources: list[str] = field(default_factory=list)
    native_call_count: int = 0
    parse_metadata: dict[str, Any] = field(default_factory=dict)

    def as_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tool_call_source": self.selected_source,
            "tool_call_native_call_count": int(self.native_call_count),
            "tool_call_attempted_fallback_sources": list(
                self.attempted_fallback_sources
            ),
            "tool_call_skipped_fallback_sources": list(self.skipped_fallback_sources),
        }
        if self.parse_metadata:
            payload["tool_call_parse_metadata"] = dict(self.parse_metadata)
        return payload


def resolve_tool_call_source_precedence(
    *,
    message_payload: Any,
    fallback_sources: Sequence[ToolCallFallbackSource],
    provider_name: str | None = None,
    model_name: str | None = None,
    allowed_tool_names: Iterable[str] | None = None,
    fallback_enabled: bool,
    parser_plugin_selection: Sequence[str] | None = None,
    fallback_parser_policy: str = "",
    fallback_mode: str = "full",
) -> ToolCallSourceResolution:
    normalized_allowed_tool_names = list(allowed_tool_names or [])
    normalized_fallback_sources = [
        ToolCallFallbackSource(
            source=str(candidate.source or "").strip() or "fallback",
            text=str(candidate.text or "").strip(),
        )
        for candidate in fallback_sources
        if str(candidate.text or "").strip()
    ]
    native_result = parse_native_tool_calls(
        message_payload,
        provider_name=provider_name,
        model_name=model_name,
        allowed_tool_names=normalized_allowed_tool_names,
    )
    native_call_count = len(native_result.calls)
    if native_result.calls:
        return ToolCallSourceResolution(
            calls=list(native_result.calls),
            selected_source="native",
            skipped_fallback_sources=[
                candidate.source for candidate in normalized_fallback_sources
            ],
            native_call_count=native_call_count,
            parse_metadata=dict(native_result.metadata or {}),
        )

    collected_metadata: dict[str, Any] = {}
    attempted_fallback_sources: list[str] = []
    if fallback_enabled:
        effective_fallback_mode = str(fallback_parser_policy or "").strip().lower()
        if effective_fallback_mode not in {"structured", "full"}:
            effective_fallback_mode = str(fallback_mode or "").strip().lower()
        for candidate in normalized_fallback_sources:
            attempted_fallback_sources.append(candidate.source)
            if effective_fallback_mode == "structured":
                fallback_result = parse_structured_tool_call_envelopes(
                    candidate.text,
                    provider_name=provider_name,
                    model_name=model_name,
                    allowed_tool_names=normalized_allowed_tool_names,
                    parser_plugin_selection=parser_plugin_selection,
                )
            else:
                fallback_result = parse_fallback_tool_calls(
                    candidate.text,
                    provider_name=provider_name,
                    model_name=model_name,
                    allowed_tool_names=normalized_allowed_tool_names,
                    parser_plugin_selection=parser_plugin_selection,
                )
            if fallback_result.metadata:
                collected_metadata.update(dict(fallback_result.metadata))
            if fallback_result.calls:
                return ToolCallSourceResolution(
                    calls=list(fallback_result.calls),
                    selected_source=candidate.source,
                    attempted_fallback_sources=list(attempted_fallback_sources),
                    native_call_count=native_call_count,
                    parse_metadata=dict(collected_metadata),
                )
        return ToolCallSourceResolution(
            selected_source="none",
            attempted_fallback_sources=list(attempted_fallback_sources),
            native_call_count=native_call_count,
            parse_metadata=dict(collected_metadata),
        )

    return ToolCallSourceResolution(
        selected_source="none",
        skipped_fallback_sources=[
            candidate.source for candidate in normalized_fallback_sources
        ],
        native_call_count=native_call_count,
        parse_metadata=dict(collected_metadata),
    )
