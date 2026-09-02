from __future__ import annotations

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


__all__ = ("tool_call_body", "tool_context_hint")
