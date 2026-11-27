"""Compact, payload-free session timeline shared by the CLI and interactive shell."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from openminion.modules.storage.runtime.session_store import (
        EventRecord,
        MessageRecord,
    )

_ANSI_PATTERN: Final = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))"
)
_CONTROL_PATTERN: Final = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SPACE_PATTERN: Final = re.compile(r"\s+")
_SUMMARY_LIMIT: Final = 160
_EPOCH: Final = datetime.min.replace(tzinfo=UTC)
_MESSAGE_ACTORS: Final = {"inbound": "user", "outbound": "assistant"}
_TOOL_RESULTS_KEYS: Final = ("tool_results", "tool_calls_cumulative")
# Timeline label for a recorded outbound result, not a stored runtime event.
_REPORTED_TOOL_EVENT: Final = "tool.result.reported"
# Exact duplicate/progress records only. Each names a step already represented by
# a message row or an aggregate observation, so omitting it loses no outcome fact.
_OMITTED_EVENT_TYPES: Final = frozenset(
    {
        "run.queued",  # start transition; the inbound message row owns the turn start
        "run.running",  # progress transition between queue and the next state
        "run.responding",  # progress transition before response persistence
        "run.waiting_tool",  # one aggregate post-tool observation; tool rows carry it
        "response.persisted",  # delivery bookkeeping; the outbound message row covers it
        "response.delivered",  # delivery bookkeeping; the outbound message row covers it
    }
)
_KIND_PRIORITY: Final = {
    "tool": 0,
    "approval": 1,
    "handoff": 1,
    "message": 2,
    "event": 3,
}


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    seq: int
    timestamp: str
    kind: str
    actor: str
    summary: str
    status: str
    event_type: str
    event_id: str


def build_timeline(
    *,
    messages: Iterable[MessageRecord] = (),
    events: Iterable[EventRecord] = (),
) -> list[TimelineEntry]:
    """Build one chronological timeline from the fetched messages and events."""
    event_list = sorted(events, key=lambda event: event.id)
    candidates: list[tuple[datetime, int, int, TimelineEntry]] = []
    for source, (entry, sort_at) in enumerate(_message_candidates(messages)):
        candidates.append((sort_at, _KIND_PRIORITY[entry.kind], source, entry))
    for source, event in enumerate(event_list, start=len(candidates)):
        candidate = _event_candidate(event)
        if candidate is None:
            continue
        entry, sort_at = candidate
        candidates.append((sort_at, _KIND_PRIORITY[entry.kind], source, entry))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return [
        replace(entry, seq=seq)
        for seq, (_timestamp, _priority, _source, entry) in enumerate(candidates, 1)
    ]


def render_timeline(
    *,
    session_id: str,
    entries: Iterable[TimelineEntry],
    total: int | None = None,
) -> str:
    """Render the human timeline, or the empty marker when it has no entries."""
    items = list(entries)
    session = _text(session_id)
    if not items:
        return f"No events for session '{session}'."
    count = len(items)
    available = count if total is None else max(total, count)
    latest = f" (latest {count})" if available > count else ""
    lines = [f"Session: {session}", f"Events: {available}{latest}"]
    for entry in items:
        summary = (
            f"{entry.actor}: {entry.summary}"
            if entry.kind == "message" and entry.actor
            else entry.summary
        )
        lines.append(f"{entry.timestamp:<19}  {entry.kind:<8}  {summary}")
    return "\n".join(lines)


def timeline_payload(
    *,
    session_id: str,
    entries: Iterable[TimelineEntry],
    total: int | None = None,
) -> dict[str, Any]:
    """Return the machine timeline; raw message metadata never leaves the store."""
    items = list(entries)
    return {
        "ok": True,
        "session_id": _text(session_id),
        "event_count": max(total or 0, len(items)),
        "shown_count": len(items),
        "entries": [
            {
                "seq": entry.seq,
                "timestamp": entry.timestamp,
                "kind": entry.kind,
                "actor": entry.actor,
                "summary": entry.summary,
                "status": entry.status,
                "event_type": entry.event_type,
                "event_id": entry.event_id,
            }
            for entry in items
        ],
    }


def _message_candidates(
    messages: Iterable[MessageRecord],
) -> Iterator[tuple[TimelineEntry, datetime]]:
    for message in messages:
        metadata = message.metadata if isinstance(message.metadata, Mapping) else {}
        role = _raw(message.role)
        actor = _MESSAGE_ACTORS.get(role, _text(role))
        timestamp, sort_at = _timestamp(message.created_at)
        yield (
            TimelineEntry(
                seq=0,
                timestamp=timestamp,
                kind="message",
                actor=actor,
                summary=_text(message.body),
                status="",
                event_type=f"message.{_text(role)}" if role else "message",
                event_id=_text(message.id),
            ),
            sort_at,
        )
        for index, result in enumerate(_tool_results(metadata)):
            yield _tool_candidate(
                message_id=_text(message.id),
                index=index,
                result=result,
                observation_time=message.created_at,
            )


def _tool_results(metadata: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for key in _TOOL_RESULTS_KEYS:
        value = metadata.get(key)
        if value is None:
            continue
        results = _parse_tool_results(value)
        if results:
            return results
    return []


def _parse_tool_results(value: object) -> list[Mapping[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _tool_candidate(
    *,
    message_id: str,
    index: int,
    result: Mapping[str, Any],
    observation_time: object,
) -> tuple[TimelineEntry, datetime]:
    """Project one recorded result; never an execution claim or a payload dump."""
    name = _raw(result.get("tool_name")) or "unknown"
    ok = result.get("ok") is True
    status = "ok" if ok else "failed"
    summary = f"tool {name} reported {status}"
    error_code = "" if ok else _tool_error_code(result)
    if error_code:
        summary = f"{summary} ({error_code})"
    timestamp, sort_at = _timestamp(observation_time)
    entry = TimelineEntry(
        seq=0,
        timestamp=timestamp,
        kind="tool",
        actor="tool",
        summary=_text(summary),
        status=status,
        event_type=_REPORTED_TOOL_EVENT,
        event_id=_raw(result.get("call_id")) or f"{message_id}:tool:{index}",
    )
    return entry, sort_at


def _tool_error_code(result: Mapping[str, Any]) -> str:
    error_code = _text(result.get("error_code"), limit=64)
    if error_code:
        return error_code
    data = result.get("data")
    if isinstance(data, Mapping):
        return _text(data.get("error_code"), limit=64)
    return ""


def _event_candidate(event: EventRecord) -> tuple[TimelineEntry, datetime] | None:
    event_type = _raw(event.event_type)
    if event_type in _OMITTED_EVENT_TYPES:
        return None
    timestamp, sort_at = _timestamp(event.created_at)
    event_id = _text(event.canonical_event_id or str(event.id))
    payload = event.payload if isinstance(event.payload, Mapping) else {}
    if event_type == "policy_confirmation_prompt":
        entry = TimelineEntry(
            seq=0,
            timestamp=timestamp,
            kind="approval",
            actor="policy",
            summary=_text(payload.get("body")) or "policy confirmation prompt",
            status="pending",
            event_type=event_type,
            event_id=event_id,
        )
    elif event_type == "agent.switched":
        source = _raw(payload.get("from_agent_id"))
        target = _raw(payload.get("to_agent_id"))
        reason = _raw(payload.get("reason"))
        summary = " -> ".join(part for part in (source, target) if part)
        if reason:
            summary = f"{summary} ({reason})" if summary else reason
        entry = TimelineEntry(
            seq=0,
            timestamp=timestamp,
            kind="handoff",
            actor=_text(source) or "agent",
            summary=_text(summary) or "agent switched",
            status="",
            event_type=event_type,
            event_id=event_id,
        )
    elif event_type == "agent.bound":
        agent = _raw(payload.get("agent_id"))
        version = _raw(payload.get("profile_version"))
        summary = " ".join(part for part in ("agent", "bound", agent) if part)
        if version:
            summary = f"{summary} (profile {version})"
        entry = TimelineEntry(
            seq=0,
            timestamp=timestamp,
            kind="event",
            actor=_text(agent),
            summary=_text(summary) or "agent bound",
            status="",
            event_type=event_type,
            event_id=event_id,
        )
    else:
        entry = TimelineEntry(
            seq=0,
            timestamp=timestamp,
            kind="event",
            actor="",
            summary=_text(event_type) or "unknown event",
            status="",
            event_type=event_type,
            event_id=event_id,
        )
    return entry, sort_at


def _timestamp(value: object) -> tuple[str, datetime]:
    text = _text(value)
    if not text:
        return "", _EPOCH
    parsed = _parse_timestamp(text)
    if parsed is None:
        return _cut(text, 19), _EPOCH
    return parsed.strftime("%Y-%m-%d %H:%M:%S"), parsed


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _raw(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _text(value: object, *, limit: int = _SUMMARY_LIMIT) -> str:
    text = _SPACE_PATTERN.sub(
        " ",
        _CONTROL_PATTERN.sub(" ", _ANSI_PATTERN.sub("", _raw(value))),
    ).strip()
    return _cut(text, limit)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 3]}..."


__all__ = (
    "TimelineEntry",
    "build_timeline",
    "render_timeline",
    "timeline_payload",
)
