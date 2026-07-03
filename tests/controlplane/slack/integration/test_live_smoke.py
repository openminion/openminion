import os
from datetime import datetime, timezone

import pytest

from openminion.modules.controlplane.channels.slack.bot_api import SlackWebAPI


@pytest.mark.slack_live
def test_slack_live_smoke_posts_controlled_message() -> None:
    missing = [
        name
        for name in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_TEST_CHANNEL")
        if not os.environ.get(name)
    ]
    if missing:
        pytest.skip("missing live Slack env vars: " + ", ".join(missing))
    api = SlackWebAPI(os.environ["SLACK_BOT_TOKEN"], request_timeout_seconds=10)
    auth = api.auth_test()
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = api.chat_post_message(
        {
            "channel": os.environ["SLACK_TEST_CHANNEL"],
            "text": f"OpenMinion Slack live smoke {timestamp}",
        }
    )

    assert auth.get("user_id")
    assert result.get("ts")
