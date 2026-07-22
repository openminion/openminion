"""Cortensor response-payload parsing helpers."""

from __future__ import annotations

from typing import Any, Mapping

from ...errors import LLMCtlError
from ..message_payloads import _extract_message_text
from ..tool_calling import ToolCallFallbackSource


def _cortensor_first_choice(response_payload: Mapping[str, Any]) -> dict[str, Any]:
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMCtlError("PROVIDER_ERROR", "Cortensor response missing choices")
    first_choice = choices[0] if isinstance(choices[0], dict) else None
    if not isinstance(first_choice, dict):
        raise LLMCtlError(
            "PROVIDER_ERROR", "Cortensor response has invalid choice payload"
        )
    return first_choice


def _cortensor_fallback_sources(
    *,
    first_choice: Mapping[str, Any],
    message_payload: Any,
    response_payload: Mapping[str, Any],
) -> list[ToolCallFallbackSource]:
    sources: list[ToolCallFallbackSource] = []
    if isinstance(message_payload, dict):
        sources.append(
            ToolCallFallbackSource(
                source="message.content",
                text=_extract_message_text(message_payload.get("content")),
            )
        )
    sources.extend(
        [
            ToolCallFallbackSource(
                source="choice.text",
                text=_extract_message_text(first_choice.get("text")),
            ),
            ToolCallFallbackSource(
                source="choice.output_text",
                text=_extract_message_text(first_choice.get("output_text")),
            ),
            ToolCallFallbackSource(
                source="response.output_text",
                text=_extract_message_text(response_payload.get("output_text")),
            ),
            ToolCallFallbackSource(
                source="response.text",
                text=_extract_message_text(response_payload.get("text")),
            ),
        ]
    )
    return sources


def _cortensor_response_text(
    *,
    first_choice: Mapping[str, Any],
    message_payload: Any,
    response_payload: Mapping[str, Any],
) -> str:
    candidates: list[Any] = []
    if isinstance(message_payload, dict):
        candidates.append(message_payload.get("content"))
    candidates.extend(
        [
            first_choice.get("text"),
            first_choice.get("output_text"),
            response_payload.get("output_text"),
            response_payload.get("text"),
        ]
    )
    for candidate in candidates:
        text = _extract_message_text(candidate)
        if text:
            return text
    return ""


def _raise_empty_cortensor_response(
    *,
    first_choice: Mapping[str, Any],
    message_payload: Any,
    response_payload: Mapping[str, Any],
) -> None:
    response_str = str(response_payload)
    if "urn:" in response_str.lower() or "task_id" in response_str:
        raise LLMCtlError(
            "EMPTY_URN_CONTENT",
            "Cortensor response contains URN but no resolvable content (off-chain result pending)",
            details={"retryable": True, "urn_present": True},
        )
    has_text_field = (
        (isinstance(message_payload, dict) and "content" in message_payload)
        or ("text" in first_choice)
        or ("output_text" in first_choice)
        or ("text" in response_payload)
        or ("output_text" in response_payload)
    )
    if not has_text_field:
        raise LLMCtlError(
            "MALFORMED_PAYLOAD",
            "Cortensor response has malformed or missing payload structure",
            details={"retryable": False},
        )
    raise LLMCtlError(
        "EMPTY_PAYLOAD",
        "Cortensor response did not include text or tool calls",
        details={"retryable": True},
    )


__all__ = [
    "_cortensor_fallback_sources",
    "_cortensor_first_choice",
    "_cortensor_response_text",
    "_raise_empty_cortensor_response",
]
