from __future__ import annotations

import json
from typing import Any

from openminion.modules.brain.constants import (
    TOOL_MESSAGE_DEPTH_LIMIT,
    TOOL_MESSAGE_SEQUENCE_LIMIT,
    TOOL_MESSAGE_STRING_LIMIT,
)
from openminion.modules.brain.schemas import ActionResult
from openminion.modules.context.input_boundaries import (
    emit_boundary_event as _pidf_emit_boundary_event,
)
from openminion.modules.llm.schemas import Message


def _truncate_tool_message_text(
    value: Any, *, limit: int = TOOL_MESSAGE_STRING_LIMIT
) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[truncated]"


def _compact_tool_message_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= TOOL_MESSAGE_DEPTH_LIMIT:
        if isinstance(value, (dict, list, tuple)):
            return "[truncated]"
        return _truncate_tool_message_text(value)
    if isinstance(value, str):
        return _truncate_tool_message_text(value)
    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= TOOL_MESSAGE_SEQUENCE_LIMIT:
                compacted["__truncated__"] = f"{len(value) - index} more key(s)"
                break
            compacted[str(key)] = _compact_tool_message_value(item, depth=depth + 1)
        return compacted
    if isinstance(value, (list, tuple)):
        items = [
            _compact_tool_message_value(item, depth=depth + 1)
            for item in list(value)[:TOOL_MESSAGE_SEQUENCE_LIMIT]
        ]
        if len(value) > TOOL_MESSAGE_SEQUENCE_LIMIT:
            items.append(
                f"...[{len(value) - TOOL_MESSAGE_SEQUENCE_LIMIT} more item(s)]"
            )
        return items
    return value


def action_result_to_tool_message(
    tool_call_id: str | None,
    tool_name: str,
    action_result: ActionResult,
) -> Message:
    payload: dict[str, Any] = {
        "status": action_result.status,
        "summary": _truncate_tool_message_text(action_result.summary or ""),
    }
    if action_result.outputs:
        payload["outputs"] = _compact_tool_message_value(action_result.outputs)
    if action_result.error:
        payload["error"] = {
            "code": action_result.error.code,
            "message": _truncate_tool_message_text(action_result.error.message),
        }

    meta: dict[str, Any] = {"tool_name": tool_name}
    if tool_call_id:
        meta["tool_call_id"] = tool_call_id
    body = json.dumps(payload, ensure_ascii=False)
    _pidf_emit_boundary_event(
        "tool_output",
        body,
        seam_id="modules.brain.loop.tools.messages.action_result_to_tool_message",
        provenance_ref=tool_call_id,
    )
    return Message(
        role="tool",
        content=body,
        meta=meta,
    )


def format_blocking_tool_message(
    *,
    tool_name: str,
    reason: str,
    termination_reason: str,
) -> Message:
    return Message(
        role="tool",
        content=json.dumps(
            {
                "status": "blocked",
                "summary": reason,
                "termination_reason": termination_reason,
            },
            ensure_ascii=False,
        ),
        meta={"tool_name": tool_name},
    )
