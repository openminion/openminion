from openminion.modules.controlplane.channels.slack.slash_commands import (
    inbound_from_slash,
    pairing_candidate_token,
    parse_slash_payload,
)


def test_slash_payload_normalizes_to_shared_command() -> None:
    envelope = parse_slash_payload(
        "team_id=T1&channel_id=C1&user_id=U1&command=%2Fopenminion&text=status"
    )

    inbound = inbound_from_slash(envelope)

    assert inbound.text == "/status"
    assert inbound.chat_key == "slack:T1:channel:C1"


def test_pair_token_is_candidate_not_consumed() -> None:
    envelope = parse_slash_payload(
        {
            "team_id": "T1",
            "channel_id": "C1",
            "user_id": "U1",
            "command": "/openminion",
            "text": "pair tok123",
        }
    )

    assert pairing_candidate_token(envelope) == "tok123"
