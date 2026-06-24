import re
from typing import Iterable

from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.llm.providers.tool_calling.base import ToolCallParseResult
from openminion.modules.llm.providers.tool_calling.contracts import (
    _MINIMAX_BRACKET_TOOL_CALL_RE,
    _normalize_allowed_tool_names,
    _parse_minimax_bracket_args,
    _resolve_allowed_tool_name,
)


class MinimaxBracketToolCallParser:
    name = "minimax_bracket"

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
        parsed: list[ProviderToolCall] = []
        raw_name: str | None = None

        for match in _MINIMAX_BRACKET_TOOL_CALL_RE.finditer(raw):
            block = match.group(1)
            tool_match = re.search(r"tool\s*=>\s*[\"'](?P<name>[^\"']+)[\"']", block)
            if tool_match is None:
                tool_match = re.search(r"tool\s*=>\s*(?P<name>[^,\s}]+)", block)
            if tool_match is None:
                continue

            name_raw = tool_match.group("name").strip()
            if raw_name is None and name_raw:
                raw_name = name_raw
            args_block = ""
            args_match = re.search(r"args\s*=>\s*\{(?P<body>[\s\S]*?)\}", block)
            if args_match is not None:
                args_block = args_match.group("body")
            args = _parse_minimax_bracket_args(args_block)
            resolved_name = _resolve_allowed_tool_name(
                name_raw,
                allowed_tool_names=allowed,
                arguments=args,
            )
            if not resolved_name:
                continue

            parsed.append(
                ProviderToolCall(
                    id="",
                    name=resolved_name,
                    arguments=args,
                    source="fallback",
                )
            )

        if parsed:
            metadata = {"fallback_parse_mode": "minimax_bracket"}
            if raw_name:
                metadata["fallback_tool_name_raw"] = raw_name
            return ToolCallParseResult(calls=parsed, metadata=metadata)
        return ToolCallParseResult()
