from typing import Any, Iterable

from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.llm.providers.tool_calling.base import ToolCallParseResult
from openminion.modules.llm.providers.tool_calling.contracts import (
    _CHANNEL_ENVELOPE_GENERIC_RE,
    _CHANNEL_ENVELOPE_RE,
    _decode_json,
    _normalize_allowed_tool_names,
    _normalize_envelope_target,
    _normalize_tool_name,
    _resolve_allowed_tool_name,
)


class OpenRouterEnvelopeParser:
    name = "openrouter_envelope"

    def parse_envelope(
        self,
        text: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> ToolCallParseResult:
        raw = str(text or "").strip()
        if not raw:
            return ToolCallParseResult()

        allowed = _normalize_allowed_tool_names(allowed_tool_names)
        parsed: list[ProviderToolCall] = []
        metadata: dict[str, Any] = {"fallback_parse_mode": "channel_envelope"}

        for match in _CHANNEL_ENVELOPE_RE.finditer(raw):
            tool_name_raw = match.group("tool_name").strip()
            json_args_raw = match.group("json_args").strip()
            if metadata.get("fallback_tool_name_raw") is None:
                metadata["fallback_tool_name_raw"] = tool_name_raw
                metadata["envelope_target_raw"] = f"tool.{tool_name_raw}"

            args = _decode_json(json_args_raw)
            if not isinstance(args, dict):
                metadata["envelope_rejected_reason"] = "malformed_json_args"
                continue

            normalized_tool_name = _normalize_tool_name(tool_name_raw)
            if normalized_tool_name in {"request", "tool_request"}:
                metadata["envelope_rejected_reason"] = (
                    "unsupported_tool_request_wrapper"
                )
                continue
            resolved_name = _resolve_allowed_tool_name(
                tool_name_raw,
                allowed_tool_names=allowed,
            )
            if not resolved_name:
                metadata["envelope_rejected_reason"] = "tool_not_allowed"
                continue

            metadata["envelope_target_normalized"] = resolved_name
            parsed.append(
                ProviderToolCall(
                    id="",
                    name=resolved_name,
                    arguments=args,
                    source="fallback",
                )
            )

        if not parsed:
            for match in _CHANNEL_ENVELOPE_GENERIC_RE.finditer(raw):
                tool_target_raw = match.group("tool_target").strip()
                json_args_raw = match.group("json_args").strip()
                if metadata.get("fallback_tool_name_raw") is None:
                    metadata["fallback_tool_name_raw"] = tool_target_raw
                    metadata["envelope_target_raw"] = tool_target_raw

                normalized_name = _normalize_envelope_target(tool_target_raw)
                if not normalized_name:
                    metadata["envelope_rejected_reason"] = "unknown_target"
                    continue

                args = _decode_json(json_args_raw)
                if not isinstance(args, dict):
                    metadata["envelope_rejected_reason"] = "malformed_json_args"
                    continue

                if _normalize_tool_name(normalized_name) == "request":
                    metadata["envelope_rejected_reason"] = (
                        "unsupported_tool_request_wrapper"
                    )
                    continue
                resolved_name = _resolve_allowed_tool_name(
                    normalized_name,
                    allowed_tool_names=allowed,
                )
                if not resolved_name:
                    metadata["envelope_rejected_reason"] = "tool_not_allowed"
                    continue

                metadata["envelope_target_normalized"] = resolved_name
                parsed.append(
                    ProviderToolCall(
                        id="",
                        name=resolved_name,
                        arguments=args,
                        source="fallback",
                    )
                )

        if parsed:
            return ToolCallParseResult(calls=parsed, metadata=metadata)
        if metadata.get("fallback_tool_name_raw") or metadata.get(
            "envelope_rejected_reason"
        ):
            return ToolCallParseResult(metadata=metadata)
        return ToolCallParseResult()
