from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.modules.controlplane.channels.slack.config import from_base_config


def test_slack_config_uses_channels_dict_and_env_resolution(tmp_path: Path) -> None:
    config = OpenMinionConfig()
    config.enabled_channels = ["console", "slack"]
    config.channels = {
        "slack": {
            "botToken": "${SLACK_BOT_TOKEN}",
            "appToken": "${SLACK_APP_TOKEN}",
            "signingSecret": "${SLACK_SIGNING_SECRET}",
            "mode": "http",
        }
    }

    cfg = from_base_config(
        base_config=config,
        home_root=tmp_path,
        data_root=tmp_path,
        env={
            "SLACK_BOT_TOKEN": "xoxb-test",
            "SLACK_APP_TOKEN": "xapp-test",
            "SLACK_SIGNING_SECRET": "secret",
        },
    ).slack

    assert cfg.enabled is True
    assert cfg.mode == "http"
    assert cfg.bot_token == "xoxb-test"
    assert cfg.app_token == "xapp-test"
    assert cfg.signing_secret == "secret"
    assert cfg.state_sqlite_path.endswith("slack-state.db")


def test_slack_config_enabled_channels_is_authoritative(tmp_path: Path) -> None:
    config = OpenMinionConfig()
    config.enabled_channels = ["slack"]
    config.channels = {"slack": {"enabled": False, "botToken": "xoxb-local"}}

    cfg = from_base_config(
        base_config=config, home_root=tmp_path, data_root=tmp_path
    ).slack

    assert cfg.enabled is True
    assert cfg.bot_token == "xoxb-local"
