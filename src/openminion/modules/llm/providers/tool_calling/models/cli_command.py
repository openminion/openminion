import re
from typing import Iterable

from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.llm.providers.tool_calling.base import ToolCallParseResult
from openminion.modules.llm.providers.tool_calling.contracts import (
    _coerce_tool_arguments,
    _normalize_allowed_tool_names,
    _resolve_allowed_tool_name,
)

_CLI_TOOL_COMMAND_RE = re.compile(
    r"(?:^|\n)\s*tool\s+(?P<name>[a-zA-Z0-9._:-]+)\s+(?P<args>\{[\s\S]*?\})(?=\s*$|\n)",
    re.IGNORECASE,
)


class PlainCliToolCommandParser:
    name = "cli_command"

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
        match = _CLI_TOOL_COMMAND_RE.search(raw)
        if match is None:
            return ToolCallParseResult()

        raw_name = str(match.group("name") or "").strip()
        args = _coerce_tool_arguments(match.group("args"))
        resolved_name = _resolve_allowed_tool_name(
            raw_name,
            allowed_tool_names=allowed,
            arguments=args,
        )
        if not resolved_name:
            return ToolCallParseResult(
                metadata={
                    "fallback_parse_mode": "cli_command",
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
                "fallback_parse_mode": "cli_command",
                "fallback_tool_name_raw": raw_name,
                "fallback_tool_name_normalized": resolved_name,
            },
        )
