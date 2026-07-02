import os

import pytest


@pytest.mark.slack_live
def test_slack_live_smoke_is_explicitly_gated() -> None:
    missing = [
        name
        for name in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_TEST_CHANNEL")
        if not os.environ.get(name)
    ]
    if missing:
        pytest.skip("missing live Slack env vars: " + ", ".join(missing))
    pytest.skip("live Slack smoke scaffold present; enable send/receive after app fixture is provisioned")
