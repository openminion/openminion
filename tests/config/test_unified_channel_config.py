from __future__ import annotations

from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.base.config.parser import (
    openminion_config_from_dict,
    openminion_config_to_dict,
)
from openminion.modules.controlplane.config import (
    from_base_config as controlplane_from_base_config,
)
from openminion.modules.controlplane.channels.telegram.config import (
    from_base_config as telegram_from_base_config,
)
from tests._csc_fixtures import _csc_install_default_agent


def _base_config_with_env(*, tmp_path: Path) -> OpenMinionConfig:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.env = {
        "OPENMINION_HOME": str(tmp_path),
        "OPENMINION_DATA_ROOT": str(tmp_path / ".openminion"),
    }
    return config


def test_openminion_config_accepts_channels_field() -> None:
    config = OpenMinionConfig(channels={"telegram": {"botToken": "x"}})
    assert config.channels["telegram"]["botToken"] == "x"


def test_parser_preserves_raw_channels_without_env_resolution() -> None:
    config = openminion_config_from_dict(
        {"channels": {"telegram": {"botToken": "${TEST_TOKEN}"}}}
    )
    assert config.channels == {"telegram": {"botToken": "${TEST_TOKEN}"}}


def test_parser_defaults_channels_to_empty_dict_when_missing() -> None:
    config = openminion_config_from_dict({})
    assert config.channels == {}


def test_parser_defaults_channels_to_empty_dict_when_not_a_mapping() -> None:
    config = openminion_config_from_dict({"channels": "telegram"})
    assert config.channels == {}


def test_parser_skips_non_mapping_channel_entries() -> None:
    config = openminion_config_from_dict(
        {"channels": {"telegram": {"botToken": "x"}, "discord": "bad"}}
    )
    assert config.channels == {"telegram": {"botToken": "x"}}


def test_to_dict_round_trips_channels() -> None:
    raw = {
        "enabled_channels": ["console", "telegram"],
        "channels": {
            "telegram": {"botToken": "${BOT_TOKEN}", "mode": "polling"},
            "controlplane": {"sqlite_path": "controlplane/cp.db"},
        },
    }
    config = openminion_config_from_dict(raw)
    assert openminion_config_to_dict(config)["channels"] == raw["channels"]


def test_controlplane_from_base_config_reads_channels_dict_and_resolves_env(
    tmp_path: Path,
) -> None:
    config = _base_config_with_env(tmp_path=tmp_path)
    config.runtime.env.update(
        {
            "CP_DB_PATH": "controlplane/custom.db",
            "OPENMINION_CFG_PATH": str(tmp_path / "config.json"),
        }
    )
    config.channels = {
        "controlplane": {
            "sqlite_path": "${CP_DB_PATH}",
            "openminion_enabled": True,
            "openminion_config_path": "${OPENMINION_CFG_PATH}",
            "openminion_channel": "telegram",
        }
    }

    cfg = controlplane_from_base_config(
        base_config=config,
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
    )

    assert cfg.sqlite_path.endswith("controlplane/custom.db")
    assert cfg.openminion_enabled is True
    assert cfg.openminion_config_path == str(tmp_path / "config.json")
    assert cfg.openminion_channel == "telegram"


def test_controlplane_from_base_config_falls_back_to_existing_defaults(
    tmp_path: Path,
) -> None:
    config = _base_config_with_env(tmp_path=tmp_path)

    cfg = controlplane_from_base_config(
        base_config=config,
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
    )

    assert cfg.path_mode == "integrated_runtime"
    assert cfg.path_source == "default_integrated"
    assert cfg.sqlite_path.endswith(".openminion/controlplane/cp.db")


def test_telegram_from_base_config_reads_channels_dict_and_resolves_env(
    tmp_path: Path,
) -> None:
    config = _base_config_with_env(tmp_path=tmp_path)
    config.runtime.env["BOT_TOKEN"] = "abc123"
    config.channels = {
        "telegram": {
            "enabled": True,
            "botToken": "${BOT_TOKEN}",
            "mode": "webhook",
            "pairing": {"mode": "off"},
            "polling": {"stateSqlitePath": "telegram/poll.db"},
        }
    }

    cfg = telegram_from_base_config(
        base_config=config,
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
    ).telegram

    assert cfg.bot_token == "abc123"
    assert cfg.mode == "webhook"
    assert cfg.pairing.mode == "off"
    assert cfg.polling.state_sqlite_path.endswith("telegram/poll.db")


def test_telegram_from_base_config_does_not_double_resolve_parser_values(
    tmp_path: Path,
) -> None:
    config = openminion_config_from_dict(
        {
            "channels": {
                "telegram": {
                    "enabled": True,
                    "botToken": "${BOT_TOKEN}",
                }
            }
        }
    )
    config.runtime.env = {
        "OPENMINION_HOME": str(tmp_path),
        "OPENMINION_DATA_ROOT": str(tmp_path / ".openminion"),
        "BOT_TOKEN": "token-456",
    }

    cfg = telegram_from_base_config(
        base_config=config,
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
    ).telegram

    assert config.channels["telegram"]["botToken"] == "${BOT_TOKEN}"
    assert cfg.bot_token == "token-456"


def test_telegram_from_base_config_falls_back_to_existing_defaults(
    tmp_path: Path,
) -> None:
    config = _base_config_with_env(tmp_path=tmp_path)

    cfg = telegram_from_base_config(
        base_config=config,
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
    ).telegram

    assert cfg.enabled is False
    assert cfg.mode == "polling"
    assert cfg.polling.path_mode == "integrated_runtime"
    assert cfg.polling.state_sqlite_path.endswith(
        ".openminion/controlplane/telegram-poll-state.db"
    )


def test_telegram_from_base_config_legacy_env_token_enables_integrated_fallback(
    tmp_path: Path,
) -> None:
    config = _base_config_with_env(tmp_path=tmp_path)
    config.runtime.env["TELEGRAM_BOT_TOKEN"] = "legacy-token"

    cfg = telegram_from_base_config(
        base_config=config,
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
    ).telegram

    assert cfg.enabled is True
    assert cfg.bot_token == "legacy-token"
