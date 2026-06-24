from dataclasses import dataclass, field
from typing import Any, Mapping

from .models import OutboundMessage, ResolvedContext


@dataclass(frozen=True)
class OutboundPayload:
    message: OutboundMessage
    hints: dict[str, Any] = field(default_factory=dict)


def from_legacy_payload(
    payload: Mapping[str, Any],
    *,
    ctx: ResolvedContext | None = None,
) -> OutboundPayload:
    payload_dict = dict(payload)
    session_id = _coerce_str(
        payload_dict.get("session_id"),
        default=ctx.session_id if ctx else "",
    )
    agent_id = _coerce_str(
        payload_dict.get("agent_id"),
        default=ctx.agent_id if ctx else "",
    )
    metadata = {
        "session_id": session_id,
        "agent_id": agent_id,
        "legacy_payload": payload_dict,
    }
    message = OutboundMessage(
        outbound_id=_coerce_str(payload_dict.get("outbound_id"), default=""),
        channel=_coerce_str(payload_dict.get("channel"), default="controlplane"),
        chat_id=_coerce_str(
            payload_dict.get("chat_id"), default=ctx.chat_key if ctx else ""
        ),
        text=str(payload_dict.get("text") or ""),
        thread_id=_optional_str(payload_dict.get("thread_id")),
        reply_to=_optional_str(payload_dict.get("reply_to")),
        metadata=metadata,
    )
    hints: dict[str, Any] = {
        "payload_type": _coerce_str(payload_dict.get("type"), default="chat")
    }
    for key in ("ok", "status", "clarify"):
        if key in payload_dict:
            hints[key] = payload_dict.get(key)
    return OutboundPayload(message=message, hints=hints)


def to_legacy_payload(outbound: OutboundPayload) -> dict[str, Any]:
    legacy = outbound.message.metadata.get("legacy_payload")
    if isinstance(legacy, dict):
        return dict(legacy)
    payload: dict[str, Any] = {
        "type": str(outbound.hints.get("payload_type") or "chat"),
        "text": outbound.message.text,
        "session_id": str(outbound.message.metadata.get("session_id") or ""),
        "agent_id": str(outbound.message.metadata.get("agent_id") or ""),
    }
    if outbound.message.thread_id:
        payload["thread_id"] = outbound.message.thread_id
    if outbound.message.reply_to:
        payload["reply_to"] = outbound.message.reply_to
    if "ok" in outbound.hints:
        payload["ok"] = outbound.hints["ok"]
    if "status" in outbound.hints:
        payload["status"] = outbound.hints["status"]
    if "clarify" in outbound.hints:
        payload["clarify"] = outbound.hints["clarify"]
    return payload


def payload_type(outbound: OutboundPayload) -> str:
    return str(outbound.hints.get("payload_type") or "chat").strip().lower()


def _coerce_str(value: Any, *, default: str) -> str:
    normalized = str(value or "").strip()
    return normalized or default


def _optional_str(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None
