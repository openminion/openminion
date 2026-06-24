from typing import Any, Iterable

from openminion.modules.llm.providers.tool_calling.base import ToolCallParseResult
from openminion.modules.llm.providers.tool_calling.contracts import (
    _decode_json,
    _extract_raw_tool_name_from_payload,
    _json_payload_candidates,
    _normalize_allowed_tool_names,
    _tool_calls_from_payload,
)


class JsonFallbackToolCallParser:
    def __init__(
        self,
        *,
        allow_explicit_tool_envelopes: bool = True,
        name: str = "json_fallback",
    ) -> None:
        self.allow_explicit_tool_envelopes = allow_explicit_tool_envelopes
        self.name = name

    def parse_text(
        self,
        text: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> ToolCallParseResult:
        allowed = _normalize_allowed_tool_names(allowed_tool_names)
        metadata: dict[str, Any] = {"fallback_parse_mode": "json_payload"}
        collected = []
        seen_calls: set[tuple[str, str, str]] = set()
        seen_candidates: set[str] = set()
        for candidate in _json_payload_candidates(text):
            if candidate in seen_candidates:
                continue
            seen_candidates.add(candidate)
            payload = _decode_json(candidate)
            if payload is None:
                continue
            if not self.allow_explicit_tool_envelopes and _has_explicit_tool_envelope(
                payload
            ):
                continue
            parsed = _tool_calls_from_payload(
                payload,
                source="fallback",
                allowed_tool_names=allowed,
            )
            if parsed:
                raw_name = _extract_raw_tool_name_from_payload(payload)
                if raw_name:
                    metadata["fallback_tool_name_raw"] = raw_name
                collected.extend(
                    call for call in parsed if _tool_call_key(call) not in seen_calls
                )
                seen_calls.update(_tool_call_key(call) for call in parsed)
        if collected:
            metadata["fallback_json_blocks_collected"] = str(len(collected))
            return ToolCallParseResult(calls=collected, metadata=metadata)
        return ToolCallParseResult()


def _tool_call_key(call: Any) -> tuple[str, str, str]:
    return (
        str(getattr(call, "id", "") or ""),
        str(getattr(call, "name", "") or ""),
        repr(sorted(dict(getattr(call, "arguments", {}) or {}).items())),
    )


def _has_explicit_tool_envelope(payload: Any) -> bool:
    if isinstance(payload, list):
        return True
    return isinstance(payload, dict) and (
        isinstance(payload.get("tool_calls"), list)
        or any(
            isinstance(payload.get(key), str)
            for key in ("name", "tool_name", "tool", ":op")
        )
    )


def _looks_like_complete_tool_payload(payload: Any) -> bool:
    if isinstance(payload, list):
        return True
    return isinstance(payload, dict) and (
        isinstance(payload.get("tool_calls"), list)
        or isinstance(payload.get("name"), str)
    )
