from dataclasses import dataclass, field
from collections.abc import Iterable
from typing import Any

from openminion.modules.tool.constants import PROVIDER_TOOL_CALL_DEFAULT_SOURCE


@dataclass
class ProviderToolSpec:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    strict: bool = False


@dataclass
class ProviderToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str = ""
    source: str = PROVIDER_TOOL_CALL_DEFAULT_SOURCE
    depends_on: list[str] = field(default_factory=list)
    approval_id: str = ""


def provider_tool_schema_payload(
    tools: Iterable[object],
) -> list[dict[str, Any]]:
    """Return the exact content-bearing schema fields sent to a provider."""

    return [
        {
            "name": str(getattr(tool, "name", "")),
            "description": str(getattr(tool, "description", "")),
            "parameters": getattr(
                tool,
                "parameters",
                getattr(tool, "input_schema", {}),
            ),
            "strict": bool(getattr(tool, "strict", False)),
        }
        for tool in tools
    ]


__all__ = [
    "ProviderToolCall",
    "ProviderToolSpec",
    "provider_tool_schema_payload",
]
