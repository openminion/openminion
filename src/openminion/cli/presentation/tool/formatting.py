from __future__ import annotations

import re
from typing import Any, Mapping

from ..models import ToolEvent

_TOOL_VERBS: dict[str, tuple[str, str]] = {
    "exec.run": ("Running", "Ran"),
    "file.read": ("Reading", "Read"),
    "file.edit": ("Editing", "Edited"),
    "file.write": ("Writing", "Wrote"),
}
_TOOL_VERB_PREFIXES: tuple[tuple[str, tuple[str, str]], ...] = (
    ("fetch", ("Fetching", "Fetched")),
    ("search", ("Searching", "Searched")),
)
_DEFAULT_VERBS: tuple[str, str] = ("Running", "Ran")
_DIFF_TOOL_NAMES = frozenset({"Edit", "Write", "file.edit", "file.write"})
_HUNK_HEADER_RE = re.compile(r"^@@\s+-\d+(,\d+)?\s+\+\d+(,\d+)?\s+@@")


def verbs_for_tool(tool_name: str) -> tuple[str, str]:
    name = str(tool_name or "").strip()
    if name in _TOOL_VERBS:
        return _TOOL_VERBS[name]
    for prefix, verbs in _TOOL_VERB_PREFIXES:
        if name.startswith(prefix):
            return verbs
    return _DEFAULT_VERBS


def tool_context_hint(tool_name: str, args: Mapping[str, Any]) -> str:
    name = str(tool_name or "").strip()
    args = args or {}
    if name == "exec.run":
        return str(args.get("command", "") or "").strip()
    if name in {"file.read", "file.edit"}:
        return str(args.get("path", "") or "").strip()
    if name.startswith("fetch"):
        return str(args.get("url", "") or "").strip()
    return ""


def tool_call_body(tool_event: ToolEvent) -> str:
    hint = tool_context_hint(tool_event.tool_name, tool_event.args)
    return hint or tool_event.tool_name


def format_tool_duration(duration_ms: int | None) -> str:
    if duration_ms is None:
        return ""
    seconds = duration_ms / 1000.0
    return "<1s" if seconds < 1.0 else f"{int(seconds)}s"


def is_diff_result(tool_name: str, content: str) -> bool:
    if tool_name not in _DIFF_TOOL_NAMES or not content:
        return False
    lines = content.split("\n")
    if any(line.startswith("$ ") for line in lines[:3]):
        return False
    hunk_index = next(
        (index for index, line in enumerate(lines) if _HUNK_HEADER_RE.match(line)),
        None,
    )
    if hunk_index is None:
        return False
    return any(line.startswith(("+", "-")) for line in lines[hunk_index + 1 :])


__all__ = (
    "format_tool_duration",
    "is_diff_result",
    "tool_call_body",
    "tool_context_hint",
)
