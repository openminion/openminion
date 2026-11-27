"""Compact, payload-free session timeline shared by the CLI and interactive shell."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from openminion.modules.storage.runtime.session_store import EventRecord

_ANSI_PATTERN: Final = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))"
)
_CONTROL_PATTERN: Final = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SPACE_PATTERN: Final = re.compile(r"\s+")
_SUMMARY_LIMIT: Final = 160
_MESSAGE_ACTORS: Final = frozenset({"user", "assistant", "system"})
_APPROVAL_PREFIXES: Final = (
    "approval.",
    "policy.",
    "permission.",
    "safety.",
    "security.",
)
_HANDOFF_PREFIXES: Final = ("handoff.", "delegation.", "delegate.")
_HANDOFF_AGENT_EVENTS: Final = (
    "agent.bound",
    "agent.switched",
    "agent.handoff",
    "agent.delegated",
    "agent.transferred",
)


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


def build_timeline(events: Iterable[EventRecord]) -> list[TimelineEntry]:
    """Build chronological entries ordered by event id, one per event."""
    entries: list[TimelineEntry] = []
    for seq, event in enumerate(sorted(events, key=lambda item: item.id), start=1):
        payload = _event_payload(event)
        event_type = _shorten(event.event_type)
        kind = _event_kind(event_type)
        if kind == "message":
            actor = _message_actor(event_type, payload)
            summary = _message_summary(event_type, payload)
        elif kind == "tool":
            actor, summary = _tool_facts(event_type, payload)
        elif kind == "approval":
            actor, summary = _approval_facts(event_type, payload)
        elif kind == "handoff":
            actor, summary = _handoff_facts(event_type, payload)
        else:
            actor, summary = _event_facts(event_type, payload)
        summary = _shorten(summary) or "unknown event"
        entries.append(
            TimelineEntry(
                seq=seq,
                timestamp=_event_timestamp(event.created_at),
                kind=kind,
                actor=actor,
                summary=_shorten(summary),
                status=_event_status(event_type, payload),
                event_type=event_type,
                event_id=_shorten(event.canonical_event_id or event.id),
            )
        )
    return entries


def render_timeline(
    *,
    session_id: str,
    entries: Iterable[TimelineEntry],
    total: int | None = None,
) -> str:
    """Render the human timeline, or the empty marker when the session has none."""
    items = list(entries)
    session = _shorten(session_id)
    if not items:
        return f"No events for session '{session}'."
    count = len(items)
    shown = (
        f" (latest {count} of {total})" if total is not None and total > count else ""
    )
    lines = [f"Session: {session}", f"Events: {count}{shown}"]
    for entry in items:
        summary = entry.summary
        if entry.kind == "message" and entry.actor:
            summary = f"{entry.actor}: {summary}"
        lines.append(f"{entry.timestamp:<19}  {entry.kind:<8}  {summary}")
    return "\n".join(lines)


def timeline_payload(
    *,
    session_id: str,
    entries: Iterable[TimelineEntry],
    total: int | None = None,
) -> dict[str, Any]:
    """Return the machine timeline; raw event payloads never leave the store."""
    items = list(entries)
    return {
        "ok": True,
        "session_id": _shorten(session_id),
        "event_count": total if total is not None else len(items),
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


def _event_payload(event: EventRecord) -> Mapping[str, Any]:
    payload = event.payload
    return payload if isinstance(payload, Mapping) else {}


def _event_kind(event_type: str) -> str:
    if event_type.startswith("tool."):
        return "tool"
    if event_type.startswith(_APPROVAL_PREFIXES):
        return "approval"
    if event_type.startswith(_HANDOFF_PREFIXES) or event_type.startswith(
        _HANDOFF_AGENT_EVENTS
    ):
        return "handoff"
    if event_type.startswith("message.") or (
        event_type.startswith("turn.") and _suffix(event_type) in _MESSAGE_ACTORS
    ):
        return "message"
    return "event"


def _suffix(event_type: str) -> str:
    return event_type.rsplit(".", 1)[-1] if "." in event_type else ""


def _first_text(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        text = _sanitize(payload.get(key))
        if text:
            return text
    return ""


def _message_actor(event_type: str, payload: Mapping[str, Any]) -> str:
    suffix = _suffix(event_type)
    if suffix in _MESSAGE_ACTORS:
        return suffix
    return _first_text(payload, "role", "actor", "sender")


def _message_summary(event_type: str, payload: Mapping[str, Any]) -> str:
    return (
        _first_text(payload, "text", "content", "message")
        or _sanitize(payload.get("summary"))
        or event_type
    )


def _tool_facts(event_type: str, payload: Mapping[str, Any]) -> tuple[str, str]:
    name = _first_text(payload, "tool_name", "name", "tool")
    status = _first_text(payload, "status") or _suffix(event_type)
    actor = _first_text(payload, "actor", "agent_id") or "tool"
    return actor, " ".join(part for part in ("tool", name, status) if part)


def _approval_facts(event_type: str, payload: Mapping[str, Any]) -> tuple[str, str]:
    outcome = _first_text(payload, "status", "decision", "reason")
    tool = _first_text(payload, "tool_name", "tool", "name")
    actor = _first_text(payload, "actor", "agent_id", "requested_by") or "policy"
    if tool:
        outcome = f"{outcome} (tool {tool})" if outcome else f"tool {tool}"
    return actor, outcome or event_type


def _handoff_facts(event_type: str, payload: Mapping[str, Any]) -> tuple[str, str]:
    source = _first_text(
        payload,
        "from_agent",
        "from_agent_id",
        "from",
        "source_agent",
        "source_agent_id",
        "source",
    )
    target = _first_text(
        payload,
        "to_agent",
        "to_agent_id",
        "to",
        "target_agent",
        "target_agent_id",
        "target",
    )
    actor = source or _first_text(payload, "actor") or "agent"
    if source and target:
        summary = f"{source} -> {target}"
    else:
        summary = source or target or event_type
    return actor, summary


def _event_facts(event_type: str, payload: Mapping[str, Any]) -> tuple[str, str]:
    status = _first_text(payload, "status")
    detail = _first_text(payload, "summary", "message", "reason")
    parts = [event_type]
    for part in (status, detail):
        if part and part not in parts:
            parts.append(part)
    actor = _first_text(payload, "actor", "agent_id")
    return actor, " ".join(parts)


def _event_status(event_type: str, payload: Mapping[str, Any]) -> str:
    status = _first_text(payload, "status", "decision")
    if status:
        return _shorten(status)
    return _suffix(event_type) if _event_kind(event_type) == "tool" else ""


def _event_timestamp(value: object) -> str:
    raw = _sanitize(value)
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return _shorten(raw, limit=19)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _sanitize(value: object) -> str:
    if isinstance(value, str):
        text = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        text = str(value)
    else:
        return ""
    text = _SPACE_PATTERN.sub(
        " ", _CONTROL_PATTERN.sub(" ", _ANSI_PATTERN.sub("", text))
    )
    return text.strip()


def _shorten(value: object, *, limit: int = _SUMMARY_LIMIT) -> str:
    text = _sanitize(value)
    return text if len(text) <= limit else f"{text[: limit - 3]}..."


__all__ = (
    "TimelineEntry",
    "build_timeline",
    "render_timeline",
    "timeline_payload",
)
