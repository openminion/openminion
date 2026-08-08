import argparse
import json
import sqlite3
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


def test_slack_pair_uses_shared_controlplane_token_store(
    tmp_path: Path, capsys
) -> None:
    config_path = tmp_path / "agent.json"
    cp_db = tmp_path / ".openminion" / "controlplane" / "cp.db"
    config_path.write_text(
        json.dumps(
            {
                "enabled_channels": ["slack"],
                "channels": {
                    "controlplane": {"sqlite_path": str(cp_db)},
                    "slack": {
                        "enabled": True,
                        "botToken": "xoxb-test",
                        "pairing": {"enabled": True, "tokenTtlSeconds": 60},
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    result = slack_cli.slack_pair(
        argparse.Namespace(
            config=str(config_path),
            team_id="T1",
            channel_id="C1",
            user_id="U1",
            ttl_seconds=60,
            scopes="cp.message.read,cp.message.write",
        )
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "Slack pairing token created." in output
    assert "/openminion pair " in output
    with sqlite3.connect(cp_db) as conn:
        row = conn.execute(
            """
            SELECT channel, expected_account_id, expected_chat_key
            FROM cp_pair_tokens
            """
        ).fetchone()
    assert row == ("slack", "slack:T1:user:U1", "slack:T1:channel:C1")
