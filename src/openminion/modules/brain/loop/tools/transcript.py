"""Durable canonical tool transcript persistence and replay."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from openminion.base.logging import format_structured_event, get_logger
from openminion.modules.brain.config import TOOL_TRANSCRIPT_MAX_REPLAYED_CALLS
from openminion.modules.brain.constants import STATE_KEY_MODULE_STATE
from openminion.modules.brain.schemas import ActionError, ActionResult
from openminion.modules.llm.schemas import Message, ToolCall
from openminion.modules.runtime.sync import (
    is_turn_deadline_elapsed,
    raise_if_cancelled,
)
from openminion.modules.tool.diagnostics.events import (
    is_structural_security_tool,
    structural_result_fields,
)

from .contracts import AdaptiveToolLoopContext

_REQUEST_EVENT_IDS_KEY = "tool_transcript.request_event_ids"
_LOG = get_logger(__name__)
_RECOVERY_STORAGE_ERRORS: tuple[type[Exception], ...] = (
    OSError,
    RuntimeError,
    ValueError,
    sqlite3.Error,
)
try:
    from sqlalchemy.exc import SQLAlchemyError
except ImportError:
    pass
else:
    _RECOVERY_STORAGE_ERRORS += (SQLAlchemyError,)


def successful_replayed_tool_names(messages: list[Message]) -> frozenset[str]:
    calls = {
        str(call.id or ""): str(call.name or "").strip()
        for message in messages
        for call in message.tool_calls
        if str(call.id or "").strip() and str(call.name or "").strip()
    }
    return frozenset(
        calls[str(message.tool_call_id or "")]
        for message in messages
        if message.role == "tool"
        and message.tool_status == "success"
        and str(message.tool_call_id or "") in calls
    )


def with_replayed_tool_messages(
    context: dict[str, Any], session_api: Any, session_id: str
) -> dict[str, Any]:
    messages = replay_tool_messages(session_api, session_id)
    if not messages:
        return context
    from openminion.modules.brain.adapters.llm.request import _messages_from_context

    updated = dict(context)
    updated["messages"] = [*_messages_from_context(context), *messages]
    updated["turns"] = []
    return updated


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


def _research_invocation_fields(loop_ctx: AdaptiveToolLoopContext) -> dict[str, str]:
    research = getattr(loop_ctx.state, STATE_KEY_MODULE_STATE, {}).get("research", {})
    invocation_id = research.get("invocation_id") if research.get("child") else None
    return {"research_invocation_id": str(invocation_id)} if invocation_id else {}


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
    structural_only = bool(getattr(loop_ctx, "structural_tool_results", False))
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
                **_research_invocation_fields(loop_ctx),
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
    raise_if_cancelled()
    session_api, session_id = _session_writer(loop_ctx)
    if session_api is None:
        return
    call_id = str(tool_call.id or "").strip()
    request_event_id = dict(loop_state.scratchpad.get(_REQUEST_EVENT_IDS_KEY, {})).get(
        call_id
    )
    if not request_event_id:
        raise ValueError("canonical tool result has no persisted requested event")
    structural_only = bool(getattr(loop_ctx, "structural_tool_results", False))

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
    payload.update(_research_invocation_fields(loop_ctx))
    try:
        session_api.append_event(
            session_id,
            type=event_type,
            parent_event_id=str(request_event_id),
            payload=payload,
            actor_type="tool",
            artifact_refs=[ref.ref for ref in action_result.artifact_refs],
        )
    except ValueError as exc:
        if "session event idempotency conflict:" in str(exc):
            raise_if_cancelled()
        raise


def _has_terminal_child_event(
    reader: Any,
    session_id: str,
    *,
    request_event_id: str,
    turn_scope_id: str,
    call_id: str,
    research_invocation_id: str,
) -> bool:
    transcript = reader(session_id)
    if transcript.get("transcript_lane") != "canonical_events":
        return False
    for event in transcript.get("events", []):
        if event.get("event_type") not in {"tool.call.completed", "tool.call.blocked"}:
            continue
        if str(event.get("parent_event_id", "") or "").strip() != request_event_id:
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if (
            str(payload.get("turn_scope_id", "") or "").strip() == turn_scope_id
            and str(payload.get("call_id", "") or "").strip() == call_id
            and str(payload.get("research_invocation_id", "") or "").strip()
            == research_invocation_id
        ):
            return True
    return False


def _build_interrupted_payload(
    event: dict[str, Any],
    resolved_parents: set[str],
    *,
    scope_filter: str,
    structural_only: bool,
    is_timeout: bool,
) -> tuple[str, dict[str, Any], str, str, str] | None:
    if event.get("event_type") != "tool.call.requested":
        return None
    request_event_id = str(event.get("event_id", "") or "").strip()
    payload = event.get("payload")
    if (
        not request_event_id
        or request_event_id in resolved_parents
        or not isinstance(payload, dict)
    ):
        return None
    event_scope_id = str(payload.get("turn_scope_id", "") or "").strip()
    call_id = str(payload.get("call_id", "") or "").strip()
    if (
        not event_scope_id
        or not call_id
        or (scope_filter and event_scope_id != scope_filter)
    ):
        return None
    canonical_name = str(payload.get("canonical_name", "") or "").strip()
    security_tool = structural_only or is_structural_security_tool(canonical_name)
    research_id = str(payload.get("research_invocation_id", "") or "").strip()
    default_message = (
        "Turn timed out before tool call completed."
        if is_timeout
        else "Turn ended before tool call completed."
    )
    blocked_payload: dict[str, Any] = {
        "schema_version": 1,
        "turn_scope_id": event_scope_id,
        "call_id": call_id,
        "status": "timeout" if is_timeout else "blocked",
        "error": {
            "code": "TURN_TIMEOUT" if is_timeout else "TURN_INTERRUPTED",
            "message": (
                "security tool did not complete" if security_tool else default_message
            ),
            "details": {},
        },
    }
    if research_id:
        blocked_payload["research_invocation_id"] = research_id
    return request_event_id, blocked_payload, event_scope_id, call_id, research_id


def persist_interrupted_tool_calls(
    session_api: Any,
    session_id: str,
    *,
    turn_scope_id: str | None = None,
    structural_only: bool = False,
    timed_out: bool | None = None,
    active_exception: BaseException | None = None,
) -> int:
    normalized_session = str(session_id or "").strip()
    reader = getattr(session_api, "get_tool_transcript", None)
    writer = getattr(session_api, "append_event", None)
    scope_filter = str(turn_scope_id or "").strip()
    if (
        not normalized_session
        or not callable(reader)
        or not callable(writer)
        or (turn_scope_id is not None and not scope_filter)
    ):
        return 0
    closed = 0
    try:
        transcript = reader(normalized_session)
        if transcript.get("transcript_lane") != "canonical_events":
            return 0
        events = transcript.get("events", [])
        resolved_parents = {
            str(event.get("parent_event_id", "") or "").strip()
            for event in events
            if event.get("event_type") in {"tool.call.completed", "tool.call.blocked"}
        }
        is_timeout = (
            isinstance(active_exception, TimeoutError) or is_turn_deadline_elapsed()
            if timed_out is None
            else bool(timed_out)
        )
        for event in events:
            built = _build_interrupted_payload(
                event,
                resolved_parents,
                scope_filter=scope_filter,
                structural_only=structural_only,
                is_timeout=is_timeout,
            )
            if built is None:
                continue
            request_event_id, blocked_payload, event_scope_id, call_id, research_id = (
                built
            )
            try:
                writer(
                    normalized_session,
                    type="tool.call.blocked",
                    parent_event_id=request_event_id,
                    payload=blocked_payload,
                    actor_type="tool",
                )
            except ValueError as exc:
                if "session event idempotency conflict:" not in str(
                    exc
                ) or not _has_terminal_child_event(
                    reader,
                    normalized_session,
                    request_event_id=request_event_id,
                    turn_scope_id=event_scope_id,
                    call_id=call_id,
                    research_invocation_id=research_id,
                ):
                    raise
                resolved_parents.add(request_event_id)
                continue
            resolved_parents.add(request_event_id)
            closed += 1
        return closed
    except (NotImplementedError, RecursionError):
        raise
    except _RECOVERY_STORAGE_ERRORS as exc:
        error_type = type(exc).__name__
        _LOG.warning(
            format_structured_event(
                "brain.tool_transcript.recovery_failed",
                session_id=normalized_session,
                turn_scope_id=scope_filter or None,
                closed=closed,
                error_type=error_type,
            )
        )
        if active_exception is None:
            raise
        active_exception.add_note(
            "Interrupted tool transcript recovery failed "
            f"(error_type={error_type}; closed={closed})"
        )
        return closed


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
