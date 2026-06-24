from dataclasses import dataclass

from .constants import DEFAULT_POLICY_FILENAME

PROMPT_TOOL_STUB_LIMIT = 8
PROMPT_TOOL_DESC_MAX_CHARS = 120
PROMPT_TOOL_REQUIRED_ARG_LIMIT = 6
PROMPT_TOOL_ARG_DESC_MAX_CHARS = 96
TOOL_STUB_DESCRIPTION_MAX_CHARS = 120


@dataclass(frozen=True)
class ToolConfig:
    policy_filename: str = DEFAULT_POLICY_FILENAME


def load_config(*_args: object, **_kwargs: object) -> ToolConfig:
    return ToolConfig()


__all__ = [
    "PROMPT_TOOL_ARG_DESC_MAX_CHARS",
    "PROMPT_TOOL_DESC_MAX_CHARS",
    "PROMPT_TOOL_REQUIRED_ARG_LIMIT",
    "PROMPT_TOOL_STUB_LIMIT",
    "TOOL_STUB_DESCRIPTION_MAX_CHARS",
    "ToolConfig",
    "load_config",
]
