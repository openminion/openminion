import argparse
import json
from pathlib import Path

from openminion.modules.controlplane.channels.slack import cli as slack_cli


class FakeSlackWebAPI:
    def __init__(self, token: str) -> None:
        self.token = token

    def auth_test(self):
        return {"ok": True, "user_id": "B1", "team": "Test Team"}


def test_slack_setup_writes_unified_config(tmp_path: Path, monkeypatch, capsys) -> None:
    config_path = tmp_path / "agent.json"
    monkeypatch.setattr(slack_cli, "SlackWebAPI", FakeSlackWebAPI)

    result = slack_cli.slack_setup(
        argparse.Namespace(
            config=str(config_path),
            bot_token_ref="env:SLACK_BOT_TOKEN",
            app_token_ref="env:SLACK_APP_TOKEN",
            signing_secret_ref="env:SLACK_SIGNING_SECRET",
            bot_token_stdin=False,
            app_token_stdin=False,
            signing_secret_stdin=False,
            unsafe_bot_token=None,
            unsafe_app_token=None,
            unsafe_signing_secret=None,
            allow_tracked_secret=False,
        )
    )

    assert result == 0
    payload = json.loads(config_path.read_text())
    assert "slack" in payload["enabled_channels"]
    assert payload["channels"]["slack"]["botToken"] == "${SLACK_BOT_TOKEN}"
    assert "Tokens: [redacted]" in capsys.readouterr().out


def test_slack_pair_refuses_local_pairing_fork(capsys) -> None:
    result = slack_cli.slack_pair(argparse.Namespace())

    assert result == 2
    assert "cross-channel pairing core" in capsys.readouterr().out
