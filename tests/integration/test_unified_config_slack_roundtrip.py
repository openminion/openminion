from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.services.runtime.catalog import ExtensionCatalog
from openminion.services.runtime.lifecycle import build_channel_registry


def test_lifecycle_registers_slack_only_when_enabled(tmp_path: Path) -> None:
    config = OpenMinionConfig()
    config.enabled_channels = ["console", "slack"]
    config.channels = {"slack": {"enabled": True, "botToken": "xoxb-test"}}

    registry = build_channel_registry(
        config=config,
        home_root=tmp_path,
        data_root=tmp_path,
        logger=__import__("logging").getLogger("test"),
    )

    assert "slack" in registry.names()


def test_catalog_reports_slack_metadata_without_live_client() -> None:
    config = OpenMinionConfig()
    config.enabled_channels = ["slack"]
    config.channels = {"slack": {"enabled": True}}

    catalog = ExtensionCatalog.from_config(config)

    slack = next(record for record in catalog.channels if record.name == "slack")
    assert slack.module == "openminion.modules.controlplane.channels.slack"
