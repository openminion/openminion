from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from .constants import (
    PRIOR_TURN_CONTEXT_CHAR_LIMIT,
    PRIOR_TURN_TOOL_RESULT_CHAR_LIMIT,
)
from openminion.modules.prompting.context_blocks import PRIOR_TURN_BLOCK_HEADER


def _bounded_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _bounded_items(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    return [
        text
        for item in value
        if (text := _bounded_text(item, limit=PRIOR_TURN_CONTEXT_CHAR_LIMIT))
    ]


def render_prior_turn_context_block(hint: Mapping[str, Any] | str | None) -> str:
    if not isinstance(hint, Mapping):
        text = _bounded_text(hint, limit=PRIOR_TURN_CONTEXT_CHAR_LIMIT)
        return (
            "\n".join([PRIOR_TURN_BLOCK_HEADER, f"- assistant: {json.dumps(text)}"])
            if text
            else ""
        )

    user_text = _bounded_text(
        hint.get("user_message"), limit=PRIOR_TURN_CONTEXT_CHAR_LIMIT
    )
    assistant_text = _bounded_text(
        hint.get("assistant_message"), limit=PRIOR_TURN_CONTEXT_CHAR_LIMIT
    )
    tool_events = _bounded_items(hint.get("tool_events"))
    latest_tool_result = _bounded_text(
        hint.get("latest_tool_result"), limit=PRIOR_TURN_TOOL_RESULT_CHAR_LIMIT
    )
    if not any((user_text, assistant_text, tool_events, latest_tool_result)):
        return ""
    lines = [
        PRIOR_TURN_BLOCK_HEADER,
        "Verbatim transcript from the immediately preceding turn. Use it as context only.",
    ]
    if user_text:
        lines.append(f"- user: {json.dumps(user_text)}")
    if assistant_text:
        lines.append(f"- assistant: {json.dumps(assistant_text)}")
    lines.extend(f"- tool_event: {json.dumps(event)}" for event in tool_events[:3])
    if latest_tool_result:
        lines.append(f"- latest_tool_result: {latest_tool_result}")
    return "\n".join(lines).strip()


def latest_structured_tool_result(module_state: Mapping[str, Any] | None) -> str:
    adaptive_loop = (
        module_state.get("adaptive_loop") if isinstance(module_state, dict) else None
    )
    tool_results = (
        adaptive_loop.get("tool_results") if isinstance(adaptive_loop, dict) else None
    )
    if not isinstance(tool_results, list):
        return ""
    for item in reversed(tool_results):
        if not isinstance(item, dict) or item.get("ok") is not True:
            continue
        tool_name = str(item.get("tool_name", "") or "").strip()
        data = item.get("data")
        if tool_name and isinstance(data, (dict, list)):
            return json.dumps(
                {"tool_name": tool_name, "data": data},
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )[:PRIOR_TURN_TOOL_RESULT_CHAR_LIMIT]
    return ""


__all__ = ["latest_structured_tool_result", "render_prior_turn_context_block"]
