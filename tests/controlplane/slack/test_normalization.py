from openminion.modules.controlplane.channels.slack.command_aliases import (
    normalize_command_text,
)
from openminion.modules.controlplane.channels.slack.normalization import (
    envelope_from_event_callback,
    event_callback_from_payload,
    inbound_from_envelope,
    slack_session_scope_key,
)


def _payload(event: dict, *, event_id: str = "Ev1") -> dict:
    return {"type": "event_callback", "team_id": "T1", "event_id": event_id, "event": event}


def test_dm_message_normalizes_to_controlplane_inbound() -> None:
    callback = event_callback_from_payload(
        _payload({"type": "message", "channel": "D1", "channel_type": "im", "user": "U1", "text": "help", "ts": "1.0"})
    )
    envelope = envelope_from_event_callback(callback, bot_user_id="B1")

    inbound = inbound_from_envelope(envelope)

    assert inbound.user_key == "slack:T1:user:U1"
    assert inbound.chat_key == "slack:T1:channel:D1"
    assert inbound.text == "/help"
    assert inbound.channel == "slack"


def test_app_mention_strips_bot_and_preserves_thread_scope() -> None:
    callback = event_callback_from_payload(
        _payload(
            {
                "type": "app_mention",
                "channel": "C1",
                "channel_type": "channel",
                "user": "U1",
                "text": "<@B1> status",
                "ts": "2.0",
                "thread_ts": "1.5",
            }
        )
    )
    envelope = envelope_from_event_callback(callback, bot_user_id="B1")

    inbound = inbound_from_envelope(envelope)

    assert inbound.text == "/status"
    assert inbound.chat_key == slack_session_scope_key("T1", "C1", "1.5")
    assert inbound.thread_id == "1.5"


def test_two_slack_threads_have_distinct_chat_keys() -> None:
    assert slack_session_scope_key("T1", "C1", "1.0") != slack_session_scope_key(
        "T1", "C1", "2.0"
    )


def test_self_loop_and_broad_channel_message_drop() -> None:
    self_callback = event_callback_from_payload(
        _payload({"type": "message", "channel": "D1", "user": "B1", "text": "hi", "ts": "1"})
    )
    broad_callback = event_callback_from_payload(
        _payload({"type": "message", "channel": "C1", "user": "U1", "text": "hi", "ts": "1"})
    )

    assert envelope_from_event_callback(self_callback, bot_user_id="B1") is None
    assert envelope_from_event_callback(broad_callback, bot_user_id="B1") is None


def test_command_aliases_match_user_language() -> None:
    assert normalize_command_text("agent use minimax") == "/profile use minimax"
    assert normalize_command_text("session new") == "/session new"
