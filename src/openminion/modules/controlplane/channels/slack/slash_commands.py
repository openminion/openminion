"""Slack slash-command parsing."""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import parse_qs

from openminion.modules.controlplane.channels.slack.command_aliases import (
    normalize_slash_command_text,
)
from openminion.modules.controlplane.channels.slack.constants import CHANNEL_ID
from openminion.modules.controlplane.channels.slack.models import (
    SlackSlashCommandEnvelope,
)
from openminion.modules.controlplane.channels.slack.normalization import (
    slack_session_scope_key,
)
from openminion.modules.controlplane.contracts.inbound import (
    canonicalize_inbound_message,
)
from openminion.modules.controlplane.contracts.models import InboundMessage


def parse_slash_payload(
    raw: bytes | str | Mapping[str, Any],
) -> SlackSlashCommandEnvelope:
    data = _payload_to_dict(raw)
    return SlackSlashCommandEnvelope(
        team_id=_required(data, "team_id"),
        channel_id=_required(data, "channel_id"),
        user_id=_required(data, "user_id"),
        command=_required(data, "command"),
        text=str(data.get("text") or "").strip(),
        response_url=str(data.get("response_url") or "").strip() or None,
        trigger_id=str(data.get("trigger_id") or "").strip() or None,
        raw=dict(data),
    )


def inbound_from_slash(envelope: SlackSlashCommandEnvelope) -> InboundMessage:
    text = normalize_slash_command_text(envelope.command, envelope.text)
    chat_key = slack_session_scope_key(envelope.team_id, envelope.channel_id, None)
    metadata = {
        "team_id": envelope.team_id,
        "channel_id": envelope.channel_id,
        "command": envelope.command,
        "response_url": envelope.response_url,
    }
    return canonicalize_inbound_message(
        InboundMessage(
            user_key=f"slack:{envelope.team_id}:user:{envelope.user_id}",
            chat_key=chat_key,
            text=text,
            channel=CHANNEL_ID,
            chat_id=envelope.channel_id,
            user_id=envelope.user_id,
            metadata=metadata,
            meta=metadata,
        )
    )


def pairing_candidate_token(envelope: SlackSlashCommandEnvelope) -> str | None:
    text = normalize_slash_command_text(envelope.command, envelope.text)
    parts = text.split()
    if len(parts) == 2 and parts[0] == "/pair":
        return parts[1]
    if len(parts) == 3 and parts[0] == "/openminion" and parts[1] == "pair":
        return parts[2]
    if len(parts) == 2 and parts[0].lower() == "pair":
        return parts[1]
    return None


def _payload_to_dict(raw: bytes | str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}
    body = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _required(data: Mapping[str, Any], key: str) -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise ValueError(f"missing Slack slash command field: {key}")
    return value
