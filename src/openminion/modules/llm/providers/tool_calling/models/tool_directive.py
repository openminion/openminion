import re
from typing import Any, Iterable

from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.llm.providers.tool_calling.base import ToolCallParseResult
from openminion.modules.llm.providers.tool_calling.contracts import (
    _coerce_minimax_parameter_value,
    _normalize_allowed_tool_names,
    _resolve_allowed_tool_name,
)

_TOOL_LINE_RE = re.compile(
    r"(?:^|\n)\s*Tool\s*:\s*(?P<name>[a-zA-Z0-9._:-]+)\s*(?:\n|$)",
    re.IGNORECASE,
)
_ARG_LINE_RE = re.compile(r"^\s*-\s*(?P<key>[a-zA-Z0-9_\-]+)\s*:\s*(?P<value>.+?)\s*$")


class PlainToolDirectiveParser:
    name = "tool_directive"

    def parse_text(
        self,
        text: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> ToolCallParseResult:
        raw = str(text or "").strip()
        if not raw:
            return ToolCallParseResult()

        allowed = _normalize_allowed_tool_names(allowed_tool_names)
        match = _TOOL_LINE_RE.search(raw)
        if match is None:
            return ToolCallParseResult()

        raw_name = str(match.group("name") or "").strip()

        args: dict[str, Any] = {}
        tail = raw[match.end() :]
        seen_arg = False
        for line in tail.splitlines():
            line = str(line or "")
            if not line.strip():
                if seen_arg:
                    break
                continue
            arg_match = _ARG_LINE_RE.match(line)
            if arg_match is None:
                if seen_arg:
                    break
                continue
            seen_arg = True
            key = str(arg_match.group("key") or "").strip()
            raw_value = str(arg_match.group("value") or "").strip()
            if not key:
                continue
            if (raw_value.startswith('"') and raw_value.endswith('"')) or (
                raw_value.startswith("'") and raw_value.endswith("'")
            ):
                raw_value = raw_value[1:-1]
            args[key] = _coerce_minimax_parameter_value(raw_value)

        resolved_name = _resolve_allowed_tool_name(
            raw_name,
            allowed_tool_names=allowed,
            arguments=args,
        )
        if not resolved_name:
            return ToolCallParseResult(
                metadata={
                    "fallback_parse_mode": "tool_directive",
                    "fallback_tool_name_raw": raw_name,
                    "fallback_rejected_reason": "tool_not_allowed",
                }
            )

        return ToolCallParseResult(
            calls=[
                ProviderToolCall(
                    id="",
                    name=resolved_name,
                    arguments=args,
                    source="fallback",
                )
            ],
            metadata={
                "fallback_parse_mode": "tool_directive",
                "fallback_tool_name_raw": raw_name,
                "fallback_tool_name_normalized": resolved_name,
            },
        )
