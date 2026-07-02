import pytest

from openminion.modules.controlplane.channels.slack.bot_api import (
    SlackAPIError,
    SlackWebAPI,
)


def test_slack_web_api_unwraps_success_response() -> None:
    api = SlackWebAPI("xoxb-secret", http_post=lambda *_: {"ok": True, "user_id": "B1"})

    assert api.auth_test()["user_id"] == "B1"


def test_slack_web_api_raises_useful_error() -> None:
    api = SlackWebAPI(
        "xoxb-secret", http_post=lambda *_: {"ok": False, "error": "not_in_channel"}
    )

    with pytest.raises(SlackAPIError) as exc:
        api.chat_post_message({"channel": "C1", "text": "hi"})

    assert "not_in_channel" in str(exc.value)
    assert exc.value.error_code == "not_in_channel"


def test_slack_web_api_redacts_token() -> None:
    assert SlackWebAPI("xoxb-secret").redacted_token() == "[redacted]"
