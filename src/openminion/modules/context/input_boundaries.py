import json
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, Field

from .constants import INPUT_BOUNDARY_LEDGER_MAX_EVENTS as _LEDGER_MAX


InputSource = Literal[
    "user_message",
    "tool_output",
    "memory_recall",
    "web_fetch",
    "search_result",
    "file_read",
    "skill_prompt",
    "gateway_system_context",
]


EscapePolicy = Literal[
    "passthrough",
    "fence_block",
    "marker_wrap",
    "json_string",
]


SOURCE_ESCAPE_POLICY: Mapping[InputSource, EscapePolicy] = MappingProxyType(
    {
        "user_message": "passthrough",
        "tool_output": "fence_block",
        "memory_recall": "fence_block",
        "web_fetch": "marker_wrap",
        "search_result": "marker_wrap",
        "file_read": "fence_block",
        "skill_prompt": "passthrough",
        "gateway_system_context": "marker_wrap",
    }
)

_RENDER_MARKERS: Mapping[InputSource, str] = MappingProxyType(
    {
        "user_message": "USER MESSAGE",
        "tool_output": "TOOL OUTPUT",
        "memory_recall": "MEMORY CARD",
        "web_fetch": "WEB FETCH",
        "search_result": "SEARCH RESULT",
        "file_read": "FILE READ",
        "skill_prompt": "SKILL SNIPPET",
        "gateway_system_context": "GATEWAY CONTEXT",
    }
)


class InputEnvelope(BaseModel):
    """Typed wrapper around any cross-boundary ingested content.

    `raw_content` preserves the original payload before escaping.
    """

    source: InputSource
    content: str
    raw_content: str
    escape_policy: EscapePolicy
    content_type: str | None = None
    provenance_ref: str | None = None
    ingested_at: str = Field(default="")


class InputBoundaryEvent(BaseModel):
    """Audit event emitted for each cross-boundary ingestion."""

    event_id: str
    source: InputSource
    escape_policy: EscapePolicy
    content_size_bytes: int
    provenance_ref: str | None = None
    seam_id: str
    recorded_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def wrap_input_content(
    source: InputSource,
    content: Any,
    *,
    provenance_ref: str | None = None,
    content_type: str | None = None,
) -> InputEnvelope:
    """Wrap raw ingestion content in a typed envelope.

    Classification is by `source` only. The per-source escape policy is
    looked up from the frozen `SOURCE_ESCAPE_POLICY` map.
    """

    if source not in SOURCE_ESCAPE_POLICY:
        raise ValueError(f"unknown InputSource: {source!r}")
    text = "" if content is None else str(content)
    return InputEnvelope(
        source=source,
        content=text,
        raw_content=text,
        escape_policy=SOURCE_ESCAPE_POLICY[source],
        content_type=content_type,
        provenance_ref=provenance_ref,
        ingested_at=_now_iso(),
    )


def escape_untrusted_content(envelope: InputEnvelope) -> InputEnvelope:
    """Apply the per-source escape policy."""

    policy = envelope.escape_policy
    raw = envelope.raw_content
    if policy == "passthrough":
        escaped = raw
    elif policy == "fence_block":
        escaped = _fence_block(raw)
    elif policy == "marker_wrap":
        escaped = _marker_wrap(envelope.source, raw)
    elif policy == "json_string":
        escaped = json.dumps(raw, ensure_ascii=False)
    else:
        raise ValueError(f"unknown EscapePolicy: {policy!r}")
    return envelope.model_copy(update={"content": escaped})


def record_input_boundary_event(
    envelope: InputEnvelope,
    *,
    seam_id: str,
    audit_log: Callable[[InputBoundaryEvent], None] | None = None,
) -> InputBoundaryEvent:
    """Emit an `InputBoundaryEvent` for a wrapped envelope."""

    if not isinstance(seam_id, str) or not seam_id.strip():
        raise ValueError("seam_id must be a non-empty constant string")
    event = InputBoundaryEvent(
        event_id=uuid.uuid4().hex,
        source=envelope.source,
        escape_policy=envelope.escape_policy,
        content_size_bytes=len(envelope.content.encode("utf-8")),
        provenance_ref=envelope.provenance_ref,
        seam_id=seam_id,
        recorded_at=_now_iso(),
    )
    if audit_log is not None:
        audit_log(event)
    return event


def render_envelope_for_prompt(envelope: InputEnvelope) -> str:
    """Render an envelope as a prompt-ready string with closed-set markers."""

    if envelope.escape_policy == "passthrough":
        return envelope.content
    marker = _RENDER_MARKERS[envelope.source]
    return f"[{marker}]\n{envelope.content}"


def route_input(
    source: InputSource,
    content: Any,
    *,
    seam_id: str,
    provenance_ref: str | None = None,
    content_type: str | None = None,
    audit_log: Callable[[InputBoundaryEvent], None] | None = None,
) -> tuple[str, InputBoundaryEvent]:
    """Convenience composition: wrap -> escape -> record -> render."""

    envelope = wrap_input_content(
        source,
        content,
        provenance_ref=provenance_ref,
        content_type=content_type,
    )
    escaped = escape_untrusted_content(envelope)
    event = record_input_boundary_event(
        escaped,
        seam_id=seam_id,
        audit_log=audit_log,
    )
    rendered = render_envelope_for_prompt(escaped)
    return rendered, event


def _fence_block(content: str) -> str:
    # Use a deterministic fence; pick a longer fence if `content` already
    # contains the default 3-backtick fence so we never collide.
    fence = "```"
    while fence in content:
        fence += "`"
    return f"{fence}\n{content}\n{fence}"


def _marker_wrap(source: InputSource, content: str) -> str:
    label = _RENDER_MARKERS[source]
    open_tag = f"<<{label}>>"
    close_tag = f"<</{label}>>"
    return f"{open_tag}\n{content}\n{close_tag}"


_event_ledger: list[InputBoundaryEvent] = []


def append_to_ledger(event: InputBoundaryEvent) -> None:
    """Append an event to the bounded process-local ledger."""

    _event_ledger.append(event)
    overflow = len(_event_ledger) - _LEDGER_MAX
    if overflow > 0:
        del _event_ledger[:overflow]


def drain_ledger() -> list[InputBoundaryEvent]:
    """Return and clear the ledger. Tests use this for parity assertions."""

    snapshot = list(_event_ledger)
    _event_ledger.clear()
    return snapshot


def snapshot_ledger() -> list[InputBoundaryEvent]:
    """Return a copy of the ledger without clearing."""

    return list(_event_ledger)


def emit_boundary_event(
    source: InputSource,
    content: Any,
    *,
    seam_id: str,
    provenance_ref: str | None = None,
    content_type: str | None = None,
) -> InputBoundaryEvent:
    """Wrap + escape + record an ingestion without forcing render mutation."""

    envelope = wrap_input_content(
        source,
        content,
        provenance_ref=provenance_ref,
        content_type=content_type,
    )
    escaped = escape_untrusted_content(envelope)
    return record_input_boundary_event(
        escaped,
        seam_id=seam_id,
        audit_log=append_to_ledger,
    )


def route_and_ledger(
    source: InputSource,
    content: Any,
    *,
    seam_id: str,
    provenance_ref: str | None = None,
    content_type: str | None = None,
) -> tuple[str, InputBoundaryEvent]:
    """`route_input` variant that writes the audit event to the ledger.

    Seam integration helper -- the standard one-call entrypoint for
    production seams that do not already plumb a custom audit_log.
    """

    return route_input(
        source,
        content,
        seam_id=seam_id,
        provenance_ref=provenance_ref,
        content_type=content_type,
        audit_log=append_to_ledger,
    )


__all__ = [
    "EscapePolicy",
    "InputBoundaryEvent",
    "InputEnvelope",
    "InputSource",
    "SOURCE_ESCAPE_POLICY",
    "append_to_ledger",
    "drain_ledger",
    "emit_boundary_event",
    "escape_untrusted_content",
    "record_input_boundary_event",
    "render_envelope_for_prompt",
    "route_and_ledger",
    "route_input",
    "snapshot_ledger",
    "wrap_input_content",
]
