from __future__ import annotations

import json
from typing import Any

from openminion.modules.brain.config import TOOL_TRANSCRIPT_MAX_REPLAYED_CALLS
from openminion.modules.brain.schemas import ActionError, ActionResult
from openminion.modules.llm.schemas import Message, ToolCall
from openminion.modules.tool.diagnostics.events import (
    is_structural_security_agent,
    is_structural_security_tool,
    structural_result_fields,
)

from .contracts import AdaptiveToolLoopContext

_REQUEST_EVENT_IDS_KEY = "tool_transcript.request_event_ids"


def _persisted_arguments(
    tool_call: ToolCall, *, structural_only: bool = False
) -> dict[str, Any]:
    if structural_only or is_structural_security_tool(tool_call.name):
        return {}
    return dict(tool_call.arguments)


def _persisted_success_output(
    tool_call: ToolCall,
    action_result: ActionResult,
    *,
    structural_only: bool = False,
) -> dict[str, Any]:
    if structural_only or is_structural_security_tool(tool_call.name):
        return {
            "summary": "security tool completed",
            "outputs": structural_result_fields(action_result.outputs),
        }
    return {
        "summary": action_result.summary,
        "outputs": dict(action_result.outputs),
    }


def replay_tool_messages(
    session_api: Any,
    session_id: str,
    *,
    max_calls: int = TOOL_TRANSCRIPT_MAX_REPLAYED_CALLS,
) -> list[Message]:
    reader = getattr(session_api, "get_tool_transcript", None)
    if not callable(reader) or not str(session_id or "").strip():
        return []
    transcript = reader(session_id)
    if transcript.get("transcript_lane") != "canonical_events":
        return []

    groups: list[list[Message]] = []
    calls: list[ToolCall] = []
    results: list[Message] = []

    def flush_group() -> None:
        nonlocal calls, results
        if not calls:
            return
        groups.append(
            [
                Message(
                    role="assistant",
                    tool_calls=calls,
                    meta={"transcript_lane": "canonical_events"},
                ),
                *results,
            ]
        )
        calls = []
        results = []

    for event in transcript.get("events", []):
        event_type = str(event.get("event_type", "") or "")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if event_type == "tool.call.requested":
            if results:
                flush_group()
            calls.append(
                ToolCall(
                    id=str(payload.get("call_id", "") or ""),
                    name=str(payload.get("canonical_name", "") or ""),
                    arguments=dict(
                        payload.get("sanitized_normalized_arguments", {}) or {}
                    ),
                    batch_index=int(payload.get("batch_index", 0) or 0),
                    depends_on=list(payload.get("depends_on", []) or []),
                )
            )
            continue
        if event_type not in {"tool.call.completed", "tool.call.blocked"}:
            continue
        status = str(payload.get("status", "") or "")
        output = payload.get("output") if event_type == "tool.call.completed" else None
        error = payload.get("error") if event_type == "tool.call.blocked" else None
        result_payload = output if output is not None else error
        results.append(
            Message(
                role="tool",
                content=json.dumps(
                    result_payload,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                tool_call_id=str(payload.get("call_id", "") or ""),
                tool_status=status,
                tool_output=output,
                tool_error=dict(error) if isinstance(error, dict) else None,
                meta={"transcript_lane": "canonical_events"},
            )
        )
    flush_group()

    retained: list[list[Message]] = []
    retained_calls = 0
    for group in reversed(groups):
        group_calls = len(group[0].tool_calls)
        if retained and retained_calls + group_calls > max(1, max_calls):
            break
        retained.append(group)
        retained_calls += group_calls
    return [message for group in reversed(retained) for message in group]


def _session_writer(loop_ctx: AdaptiveToolLoopContext) -> tuple[Any | None, str]:
    session_api = getattr(loop_ctx, "session_api", None)
    session_id = str(getattr(loop_ctx.state, "session_id", "") or "")
    if (
        session_api is None
        or not callable(getattr(session_api, "append_event", None))
        or not session_id
    ):
        return None, ""
    return session_api, session_id


def persist_requested_tool_calls(
    loop_ctx: AdaptiveToolLoopContext,
    *,
    loop_state: Any,
    turn_scope_id: str,
    tool_calls: list[ToolCall],
) -> None:
    session_api, session_id = _session_writer(loop_ctx)
    if session_api is None:
        return
    structural_only = is_structural_security_agent(
        getattr(loop_ctx.state, "agent_id", "")
    )
    event_ids = dict(loop_state.scratchpad.get(_REQUEST_EVENT_IDS_KEY, {}))
    for batch_index, call in enumerate(tool_calls):
        call_id = str(call.id or "").strip()
        if not call_id:
            raise ValueError("canonical tool call is missing call_id")
        event_ids[call_id] = session_api.append_event(
            session_id,
            type="tool.call.requested",
            payload={
                "schema_version": 1,
                "turn_scope_id": turn_scope_id,
                "call_id": call_id,
                "canonical_name": call.name,
                "sanitized_normalized_arguments": _persisted_arguments(
                    call, structural_only=structural_only
                ),
                "batch_index": batch_index,
                "depends_on": list(call.depends_on),
            },
            actor_type="agent",
        )
    loop_state.scratchpad[_REQUEST_EVENT_IDS_KEY] = event_ids


def persist_terminal_tool_result(
    loop_ctx: AdaptiveToolLoopContext,
    *,
    loop_state: Any,
    turn_scope_id: str,
    tool_call: ToolCall,
    action_result: ActionResult,
) -> None:
    session_api, session_id = _session_writer(loop_ctx)
    if session_api is None:
        return
    call_id = str(tool_call.id or "").strip()
    request_event_id = dict(loop_state.scratchpad.get(_REQUEST_EVENT_IDS_KEY, {})).get(
        call_id
    )
    if not request_event_id:
        raise ValueError("canonical tool result has no persisted requested event")
    structural_only = is_structural_security_agent(
        getattr(loop_ctx.state, "agent_id", "")
    )

    if action_result.status == "success":
        event_type = "tool.call.completed"
        payload = {
            "schema_version": 1,
            "turn_scope_id": turn_scope_id,
            "call_id": call_id,
            "status": "success",
            "output": _persisted_success_output(
                tool_call, action_result, structural_only=structural_only
            ),
        }
    else:
        status = {
            "timeout": "timeout",
            "blocked": "blocked",
            "needs_user": "blocked",
            "failed": "error",
            "retry": "error",
        }[action_result.status]
        error = action_result.error
        event_type = "tool.call.blocked"
        security_tool = structural_only or is_structural_security_tool(tool_call.name)
        payload = {
            "schema_version": 1,
            "turn_scope_id": turn_scope_id,
            "call_id": call_id,
            "status": status,
            "error": {
                "code": error.code if error else "TOOL_EXECUTION_FAILED",
                "message": (
                    "security tool did not complete"
                    if security_tool
                    else error.message
                    if error
                    else action_result.summary or status
                ),
                "details": {}
                if security_tool
                else dict(error.details)
                if error
                else {},
            },
        }
    session_api.append_event(
        session_id,
        type=event_type,
        parent_event_id=str(request_event_id),
        payload=payload,
        actor_type="tool",
        artifact_refs=[ref.ref for ref in action_result.artifact_refs],
    )


def persist_blocked_tool_calls(
    loop_ctx: AdaptiveToolLoopContext,
    *,
    loop_state: Any,
    turn_scope_id: str,
    tool_calls: list[ToolCall],
    code: str,
    message: str,
) -> list[ActionResult]:
    results = []
    for tool_call in tool_calls:
        result = ActionResult(
            command_id=str(tool_call.id or tool_call.name),
            status="blocked",
            summary=message,
            error=ActionError(code=code, message=message),
        )
        persist_terminal_tool_result(
            loop_ctx,
            loop_state=loop_state,
            turn_scope_id=turn_scope_id,
            tool_call=tool_call,
            action_result=result,
        )
        results.append(result)
    return results
