"""Helpers for direct-tool routing reason ownership."""

from __future__ import annotations

from typing import Any

EXPLICIT_DIRECT_TOOL_REASON_CODES = frozenset(
    {
        "explicit_tool_command",
        "explicit_agent_command",
        "forced_tool_command",
    }
)


def is_explicit_direct_tool_reason(reason_code: Any) -> bool:
    return str(reason_code or "").strip() in EXPLICIT_DIRECT_TOOL_REASON_CODES
