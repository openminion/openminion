from openminion.modules.controlplane.channels.slack.access import SlackAccessPolicy
from openminion.modules.controlplane.channels.slack.config import SlackAccessConfig
from openminion.modules.controlplane.channels.slack.models import SlackInboundEnvelope


def _env(**overrides):
    data = {
        "team_id": "T1",
        "channel_id": "D1",
        "user_id": "U1",
        "text": "hi",
        "ts": "1.0",
        "event_id": "Ev1",
        "event_type": "message",
        "channel_type": "im",
    }
    data.update(overrides)
    return SlackInboundEnvelope(**data)


def test_access_allows_dm_and_app_mention() -> None:
    policy = SlackAccessPolicy()

    assert policy.evaluate(_env()).allowed is True
    assert policy.evaluate(
        _env(event_type="app_mention", channel_type="channel", channel_id="C1")
    ).allowed is True


def test_access_denies_channel_without_mention_and_allowlist_miss() -> None:
    assert (
        SlackAccessPolicy().evaluate(
            _env(channel_id="C1", channel_type="channel")
        ).reason
        == "app_mention_required"
    )
    assert (
        SlackAccessPolicy(SlackAccessConfig(allowed_team_ids=("T2",)))
        .evaluate(_env())
        .reason
        == "team_allowlist_miss"
    )
