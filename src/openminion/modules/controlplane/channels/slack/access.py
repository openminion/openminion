"""Slack access policy for adapter-level filtering."""

from __future__ import annotations

from dataclasses import dataclass

from openminion.modules.controlplane.channels.slack.config import SlackAccessConfig
from openminion.modules.controlplane.channels.slack.models import SlackInboundEnvelope


@dataclass(frozen=True)
class SlackAccessDecision:
    allowed: bool
    reason: str = "ok"


class SlackAccessPolicy:
    def __init__(self, config: SlackAccessConfig | None = None) -> None:
        self._config = config or SlackAccessConfig()

    def evaluate(self, envelope: SlackInboundEnvelope) -> SlackAccessDecision:
        if (
            self._config.allowed_team_ids
            and envelope.team_id not in self._config.allowed_team_ids
        ):
            return SlackAccessDecision(False, "team_allowlist_miss")
        if (
            self._config.allowed_channel_ids
            and envelope.channel_id not in self._config.allowed_channel_ids
        ):
            return SlackAccessDecision(False, "channel_allowlist_miss")
        if envelope.bot_id or envelope.user_id == envelope.bot_user_id:
            return SlackAccessDecision(False, "self_or_bot_message")
        if envelope.channel_type == "im" and self._config.allow_dms:
            return SlackAccessDecision(True)
        if envelope.event_type == "app_mention":
            return SlackAccessDecision(True)
        if self._config.allow_broad_channel_messages:
            return SlackAccessDecision(True)
        return SlackAccessDecision(False, "app_mention_required")
