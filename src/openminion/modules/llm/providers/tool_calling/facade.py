"""Public helper facade for provider tool-call parsing."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from openminion.modules.llm.providers.base import ProviderToolCall

from .registry import parse_fallback_tool_calls, parse_native_tool_calls


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
