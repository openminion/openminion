"""Slack inbound normalization into controlplane contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from openminion.modules.controlplane.channels.slack.command_aliases import (
    normalize_command_text,
    strip_bot_mention,
)
from openminion.modules.controlplane.channels.slack.constants import CHANNEL_ID
from openminion.modules.controlplane.channels.slack.models import (
    SlackEventCallback,
    SlackInboundEnvelope,
    SlackReplyTarget,
)
from openminion.modules.controlplane.contracts.inbound import (
    canonicalize_inbound_message,
)
from openminion.modules.controlplane.contracts.models import InboundMessage


def slack_session_scope_key(
    team_id: str, channel_id: str, thread_ts: str | None = None
) -> str:
    base = f"slack:{team_id}:channel:{channel_id}"
    if thread_ts:
        return f"{base}:thread:{thread_ts}"
    return base


def event_callback_from_payload(payload: Mapping[str, Any]) -> SlackEventCallback | None:
    if payload.get("type") != "event_callback":
        return None
    event = payload.get("event")
    if not isinstance(event, dict):
        return None
    team_id = str(payload.get("team_id") or event.get("team") or "").strip()
    event_id = str(payload.get("event_id") or "").strip()
    if not team_id or not event_id:
        return None
    return SlackEventCallback(
        team_id=team_id,
        event_id=event_id,
        event=event,
        raw=dict(payload),
    )


def envelope_from_event_callback(
    callback: SlackEventCallback,
    *,
    bot_user_id: str | None = None,
    allow_broad_channel_messages: bool = False,
) -> SlackInboundEnvelope | None:
    event = callback.event
    event_type = str(event.get("type") or "").strip()
    subtype = str(event.get("subtype") or "").strip() or None
    if subtype:
        return None
    if event.get("bot_id") or event.get("user") == bot_user_id:
        return None
    channel_id = str(event.get("channel") or "").strip()
    user_id = str(event.get("user") or "").strip()
    ts = str(event.get("ts") or event.get("event_ts") or "").strip()
    if not channel_id or not user_id or not ts:
        return None
    channel_type = str(event.get("channel_type") or "").strip()
    text = str(event.get("text") or "")
    if event_type == "app_mention":
        text = strip_bot_mention(text, bot_user_id=bot_user_id)
    elif event_type == "message":
        if channel_type != "im" and not allow_broad_channel_messages:
            return None
    else:
        return None
    normalized_text = normalize_command_text(text)
    blocks = tuple(
        block
        for block in (event.get("blocks") or ())
        if isinstance(block, dict)
    )
    return SlackInboundEnvelope(
        team_id=callback.team_id,
        channel_id=channel_id,
        user_id=user_id,
        text=normalized_text,
        ts=ts,
        event_id=callback.event_id,
        event_type=event_type,
        channel_type=channel_type,
        thread_ts=str(event.get("thread_ts") or "").strip() or None,
        bot_id=str(event.get("bot_id") or "").strip() or None,
        bot_user_id=bot_user_id,
        subtype=subtype,
        blocks=blocks,
        raw_event=dict(event),
    )


def inbound_from_envelope(envelope: SlackInboundEnvelope) -> InboundMessage:
    chat_key = slack_session_scope_key(
        envelope.team_id, envelope.channel_id, envelope.thread_ts
    )
    metadata = {
        "team_id": envelope.team_id,
        "channel_id": envelope.channel_id,
        "channel_type": envelope.channel_type,
        "event_type": envelope.event_type,
        "event_id": envelope.event_id,
        "ts": envelope.ts,
    }
    inbound = InboundMessage(
        user_key=f"slack:{envelope.team_id}:user:{envelope.user_id}",
        chat_key=chat_key,
        text=envelope.text,
        channel=CHANNEL_ID,
        thread_key=envelope.thread_ts,
        inbound_id=envelope.event_id,
        channel_message_id=envelope.ts,
        chat_id=envelope.channel_id,
        user_id=envelope.user_id,
        thread_id=envelope.thread_ts,
        reply_to=envelope.ts,
        timestamp=_timestamp_from_slack_ts(envelope.ts),
        metadata=metadata,
        meta=metadata,
    )
    return canonicalize_inbound_message(inbound)


def to_reply_target(envelope: SlackInboundEnvelope) -> SlackReplyTarget:
    return SlackReplyTarget(
        channel_id=envelope.channel_id,
        thread_ts=envelope.thread_ts,
        reply_to=envelope.ts,
    )


def _timestamp_from_slack_ts(ts: str) -> datetime:
    try:
        seconds = float(ts)
    except ValueError:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(seconds, tz=timezone.utc)
