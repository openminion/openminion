from dataclasses import dataclass, field
from typing import Any
from collections.abc import Iterable, Sequence

from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.llm.providers.tool_calling.base import (
    ERROR_INVALID_TOOL_ARGUMENTS,
    ERROR_UNKNOWN_TOOL_NAME,
    ERROR_UNPARSEABLE_TOOL_ENVELOPE,
    ToolCallParseError,
    ToolCallParseResult,
)
from openminion.modules.llm.providers.tool_calling.registry import (
    parse_fallback_tool_calls,
    parse_native_tool_calls,
)
from openminion.modules.llm.providers.tool_calling.contracts import (
    _coerce_tool_arguments,
    _resolve_allowed_tool_name,
    detect_raw_envelope,
    detect_raw_tool_markup,
)

PARSE_STRATEGY_KEY = "tool_parse_strategy"
PARSE_FORMAT_KEY = "tool_parse_format"
PARSE_ERRORS_KEY = "tool_parse_errors"


@dataclass
class NormalizedToolCallResult:
    """Caller-facing shape mandated by contract §6.2."""

    calls: list[ProviderToolCall] = field(default_factory=list)
    errors: list[ToolCallParseError] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolCallNormalizer:
    """Single normalizer that maps provider output -> v2-aligned envelope.

    Single-instance + module-level convenience are both supported so callers
    can either inject the normalizer or use the module-level helper.
    """

    def normalize(
        self,
        *,
        message_payload: Any = None,
        assistant_text: str | None = None,
        provider_name: str | None = None,
        model_name: str | None = None,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> NormalizedToolCallResult:
        normalized_allowed = (
            list(allowed_tool_names) if allowed_tool_names is not None else None
        )

        native_result = parse_native_tool_calls(
            message_payload,
            provider_name=provider_name,
            model_name=model_name,
            allowed_tool_names=normalized_allowed,
        )
        if native_result.calls:
            return _finalize_result(
                base=native_result,
                strategy="native",
                fallback_strategy_used=False,
            )

        unknown_native = _detect_unknown_native_tool_name(
            message_payload, allowed_tool_names=normalized_allowed
        )

        text = str(assistant_text or "").strip()
        fallback_result = ToolCallParseResult()
        if text:
            fallback_result = parse_fallback_tool_calls(
                text,
                provider_name=provider_name,
                model_name=model_name,
                allowed_tool_names=normalized_allowed,
            )

        if fallback_result.calls:
            result = _finalize_result(
                base=fallback_result,
                strategy="hybrid" if native_result.metadata else "fallback",
                fallback_strategy_used=True,
            )
            if unknown_native:
                result.errors.append(unknown_native)
                result.metadata[PARSE_ERRORS_KEY] = [
                    _error_to_dict(error) for error in result.errors
                ]
            return result

        errors: list[ToolCallParseError] = []
        if unknown_native is not None:
            errors.append(unknown_native)

        unknown_fallback = _detect_unknown_fallback_tool_name(fallback_result.metadata)
        if unknown_fallback is not None:
            errors.append(unknown_fallback)

        invalid_args = _detect_invalid_tool_arguments(fallback_result.metadata)
        if invalid_args is not None:
            errors.append(invalid_args)

        unparseable = _detect_unparseable_envelope(
            text,
            metadata=fallback_result.metadata,
            had_unknown_match=any(
                error.code == ERROR_UNKNOWN_TOOL_NAME for error in errors
            ),
        )
        if unparseable is not None:
            errors.append(unparseable)

        metadata = dict(fallback_result.metadata or {})
        metadata[PARSE_STRATEGY_KEY] = "none"
        metadata.setdefault(PARSE_FORMAT_KEY, "")
        if errors:
            metadata[PARSE_ERRORS_KEY] = [_error_to_dict(error) for error in errors]

        return NormalizedToolCallResult(
            calls=[],
            errors=errors,
            metadata=metadata,
        )


_DEFAULT_NORMALIZER = ToolCallNormalizer()


def normalize_tool_calls(
    *,
    message_payload: Any = None,
    assistant_text: str | None = None,
    provider_name: str | None = None,
    model_name: str | None = None,
    allowed_tool_names: Iterable[str] | None = None,
) -> NormalizedToolCallResult:
    """Module-level convenience for the default normalizer."""

    return _DEFAULT_NORMALIZER.normalize(
        message_payload=message_payload,
        assistant_text=assistant_text,
        provider_name=provider_name,
        model_name=model_name,
        allowed_tool_names=allowed_tool_names,
    )


def _finalize_result(
    *,
    base: ToolCallParseResult,
    strategy: str,
    fallback_strategy_used: bool,
) -> NormalizedToolCallResult:
    metadata = dict(base.metadata or {})
    metadata[PARSE_STRATEGY_KEY] = strategy

    if PARSE_FORMAT_KEY not in metadata:
        fallback_mode = metadata.get("fallback_parse_mode")
        if fallback_mode:
            metadata[PARSE_FORMAT_KEY] = str(fallback_mode)
        else:
            metadata[PARSE_FORMAT_KEY] = (
                "openai_native" if not fallback_strategy_used else ""
            )

    errors = list(base.errors or [])
    if errors:
        metadata[PARSE_ERRORS_KEY] = [_error_to_dict(error) for error in errors]

    return NormalizedToolCallResult(
        calls=list(base.calls),
        errors=errors,
        metadata=metadata,
    )


def _detect_unknown_native_tool_name(
    message_payload: Any,
    *,
    allowed_tool_names: Sequence[str] | None,
) -> ToolCallParseError | None:
    if not isinstance(message_payload, dict):
        return None
    if allowed_tool_names is None:
        return None

    raw_calls = message_payload.get("tool_calls")
    candidate_name = ""
    candidate_arguments: dict[str, Any] = {}
    if isinstance(raw_calls, list):
        for item in raw_calls:
            if not isinstance(item, dict):
                continue
            function_payload = item.get("function")
            if isinstance(function_payload, dict):
                candidate_name = str(function_payload.get("name", "")).strip()
                candidate_arguments = _coerce_tool_arguments(
                    function_payload.get("arguments")
                )
                if candidate_name:
                    break
    if not candidate_name:
        function_call = message_payload.get("function_call")
        if isinstance(function_call, dict):
            candidate_name = str(function_call.get("name", "")).strip()
            candidate_arguments = _coerce_tool_arguments(function_call.get("arguments"))
    if not candidate_name:
        return None

    allowed_set = {
        str(name).strip() for name in allowed_tool_names if str(name).strip()
    }
    resolved = _resolve_allowed_tool_name(
        candidate_name,
        allowed_tool_names=allowed_set or None,
        arguments=candidate_arguments,
    )
    if resolved:
        return None
    return ToolCallParseError(
        code=ERROR_UNKNOWN_TOOL_NAME,
        message=(f"native tool call references unknown tool name {candidate_name!r}"),
        details={
            "tool_name": candidate_name,
            "channel": "native",
            "allowed_tool_count": len(allowed_set),
        },
    )


def _detect_unknown_fallback_tool_name(
    metadata: dict[str, Any] | None,
) -> ToolCallParseError | None:
    if not metadata:
        return None
    envelope_reason = str(metadata.get("envelope_rejected_reason", "") or "")
    fallback_reason = str(metadata.get("fallback_rejected_reason", "") or "")
    if envelope_reason != "tool_not_allowed" and fallback_reason != "tool_not_allowed":
        return None
    candidate_name = str(
        metadata.get("envelope_target_raw")
        or metadata.get("fallback_tool_name_raw")
        or ""
    ).strip()
    if not candidate_name:
        candidate_name = "<unknown>"
    return ToolCallParseError(
        code=ERROR_UNKNOWN_TOOL_NAME,
        message=(
            f"fallback tool envelope references unknown tool name {candidate_name!r}"
        ),
        details={
            "tool_name": candidate_name,
            "channel": "fallback",
            "rejected_reason": envelope_reason or fallback_reason,
        },
    )


def _detect_invalid_tool_arguments(
    metadata: dict[str, Any] | None,
) -> ToolCallParseError | None:
    if not metadata:
        return None
    if metadata.get("envelope_rejected_reason") != "malformed_json_args":
        return None
    candidate_name = str(
        metadata.get("envelope_target_raw")
        or metadata.get("fallback_tool_name_raw")
        or "<unknown>"
    ).strip()
    return ToolCallParseError(
        code=ERROR_INVALID_TOOL_ARGUMENTS,
        message="tool call arguments could not be parsed as a JSON object",
        details={
            "tool_name": candidate_name,
            "missing": [],
            "invalid": ["arguments"],
            "hint": "arguments MUST be a JSON object",
        },
    )


def _detect_unparseable_envelope(
    text: str,
    *,
    metadata: dict[str, Any] | None,
    had_unknown_match: bool,
) -> ToolCallParseError | None:
    if not text:
        return None
    if had_unknown_match:
        return None
    if metadata and metadata.get("envelope_rejected_reason") in {
        "malformed_json_args",
        "tool_not_allowed",
    }:
        return None
    if not (detect_raw_envelope(text) or detect_raw_tool_markup(text)):
        return None
    raw_target = ""
    if metadata:
        raw_target = str(
            metadata.get("envelope_target_raw")
            or metadata.get("fallback_tool_name_raw")
            or ""
        ).strip()
    return ToolCallParseError(
        code=ERROR_UNPARSEABLE_TOOL_ENVELOPE,
        message="assistant text contained raw tool envelope markup that could not be parsed",
        details={
            "tool_name": raw_target or "<unknown>",
            "reason": (
                metadata.get("envelope_rejected_reason")
                or metadata.get("fallback_rejected_reason")
                or "unparseable"
            )
            if metadata
            else "unparseable",
        },
    )


def _error_to_dict(error: ToolCallParseError) -> dict[str, Any]:
    return {
        "code": error.code,
        "message": error.message,
        "details": dict(error.details or {}),
    }


__all__ = [
    "NormalizedToolCallResult",
    "PARSE_ERRORS_KEY",
    "PARSE_FORMAT_KEY",
    "PARSE_STRATEGY_KEY",
    "ToolCallNormalizer",
    "normalize_tool_calls",
]
