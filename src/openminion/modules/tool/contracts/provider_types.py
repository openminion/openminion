from dataclasses import dataclass, field
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


__all__ = [
    "ProviderToolCall",
    "ProviderToolSpec",
]
