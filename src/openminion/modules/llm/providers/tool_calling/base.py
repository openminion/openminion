from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from openminion.modules.llm.providers.base import ProviderToolCall

ERROR_UNKNOWN_TOOL_NAME = "UNKNOWN_TOOL_NAME"
ERROR_INVALID_TOOL_ARGUMENTS = "INVALID_TOOL_ARGUMENTS"
ERROR_UNPARSEABLE_TOOL_ENVELOPE = "UNPARSEABLE_TOOL_ENVELOPE"


@dataclass
class ToolCallParseError:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallParseResult:
    calls: list[ProviderToolCall] = field(default_factory=list)
    errors: list[ToolCallParseError] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolCallParser(Protocol):
    name: str

    def parse_native(
        self,
        message_payload: Any,
        *,
        allowed_tool_names: Sequence[str] | None = None,
    ) -> ToolCallParseResult: ...

    def parse_fallback(
        self,
        text: str,
        *,
        allowed_tool_names: Sequence[str] | None = None,
    ) -> ToolCallParseResult: ...
