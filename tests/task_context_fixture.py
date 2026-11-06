"""Explicit new-task deltas for scripted providers testing unrelated contracts."""

from copy import deepcopy
import json
from typing import Any

from openminion.modules.brain.loop.context.task_context import TASK_UPDATE_INSTRUCTIONS


def replace_entry_task(
    request: dict[str, Any], message: dict[str, Any]
) -> dict[str, Any]:
    calls = message.get("tool_calls") or []
    if not calls:
        return message
    specs = {
        spec["function"]["name"]: spec["function"]["parameters"]
        for spec in request.get("tools") or []
    }
    contents = [_content(item) for item in request["messages"]]
    selected = next(
        (
            call
            for call in calls
            if "task_update"
            in specs.get(call["function"]["name"], {}).get("required", [])
        ),
        None,
    )
    if not specs and any(TASK_UPDATE_INSTRUCTIONS in content for content in contents):
        snapshot = next(
            (text for text in contents if text.startswith("CURRENT TASK (")), None
        )
        if snapshot is not None:
            # Ollama fallback transmits the entry contract in messages, without native tools.
            selected = calls[0]
    if selected is None:
        return message
    snapshot = next(
        content for content in contents if content.startswith("CURRENT TASK (")
    )
    task, _ = json.JSONDecoder().raw_decode(snapshot.split("\n", 1)[1])
    latest = _content(request["messages"][-1])
    assert request["messages"][-1]["role"] == "user" and not latest.startswith(
        "CURRENT TASK"
    )
    result = deepcopy(message)
    call = result["tool_calls"][calls.index(selected)]["function"]
    encoded = isinstance(call["arguments"], str)
    arguments = json.loads(call["arguments"]) if encoded else dict(call["arguments"])
    assert "task_update" not in arguments
    arguments["task_update"] = {
        "operation": "replace",
        "expected_revision": task["revision"],
        "user_quote": latest,
        "objective": latest,
    }
    call["arguments"] = json.dumps(arguments) if encoded else arguments
    return result


def _content(message: dict[str, Any]) -> str:
    content = message.get("content") or ""
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content)
    return str(content)
