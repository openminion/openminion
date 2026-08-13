from __future__ import annotations

from dataclasses import dataclass

from .schemas import LLMRequest


@dataclass(frozen=True, slots=True)
class ToolTranscriptError(ValueError):
    reason_code: str
    message: str

    def __str__(self) -> str:
        return self.message


def _fail(reason_code: str, message: str) -> None:
    raise ToolTranscriptError(reason_code=reason_code, message=message)


def validate_tool_transcript(request: LLMRequest) -> str:
    calls: dict[str, int] = {}
    results: set[str] = set()
    saw_legacy = False

    for message_index, message in enumerate(request.messages):
        if message.role == "assistant":
            for call in message.tool_calls:
                call_id = str(call.id or "").strip()
                if not call_id:
                    _fail("missing_call_id", "Assistant tool call is missing call_id")
                if call_id in calls:
                    _fail("duplicate_call_id", f"Duplicate tool call ID: {call_id}")
                if not str(call.name or "").strip():
                    _fail("missing_canonical_name", f"Tool call {call_id} has no name")
                calls[call_id] = message_index
            continue

        if message.role != "tool":
            continue
        meta = dict(message.meta)
        lane = str(meta.get("transcript_lane", "") or "").strip()
        call_id = str(message.tool_call_id or "").strip()
        if not call_id:
            if lane == "legacy_history" or meta.get("tool_call_id"):
                saw_legacy = True
                continue
            _fail("orphan_result", "Tool result is missing typed tool_call_id")
        if lane == "legacy_history":
            saw_legacy = True
            continue
        duplicated = {
            "arguments",
            "canonical_name",
            "tool_arguments",
            "tool_name",
        }.intersection(meta)
        if duplicated:
            _fail(
                "result_argument_duplication",
                "Canonical tool result duplicates call-owned fields: "
                + ", ".join(sorted(duplicated)),
            )
        call_index = calls.get(call_id)
        if call_index is None or call_index >= message_index:
            _fail("orphan_result", f"Tool result has no preceding call: {call_id}")
        if call_id in results:
            _fail("duplicate_result", f"Duplicate tool result: {call_id}")
        if message.tool_status is None:
            _fail("missing_result_status", f"Tool result has no status: {call_id}")
        results.add(call_id)

    if calls:
        return "canonical_events"
    if saw_legacy:
        return "legacy_history"
    strategy = str(request.metadata.get("tool_call_strategy", "") or "").lower()
    return "declared_fallback" if strategy == "fallback" else "canonical_events"
