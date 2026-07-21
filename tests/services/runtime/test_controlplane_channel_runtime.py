from __future__ import annotations

from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.modules.controlplane.channels.slack.webhook import (
    SlackHttpEventsRunner,
)
from openminion.modules.controlplane.channels.telegram.polling import (
    TelegramPollingRunner,
)
from openminion.services.runtime.lifecycle import LifecycleService, build_channel_registry
from openminion.services.security.policy import SecurityPolicyEngine
from tests._csc_fixtures import _csc_install_default_agent


def _config(tmp_path: Path, *, channels: list[str]) -> OpenMinionConfig:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.enabled_channels = ["console", *channels]
    config.runtime.env = {
        "OPENMINION_HOME": str(tmp_path),
        "OPENMINION_DATA_ROOT": str(tmp_path / ".openminion"),
    }
    config.channels = {
        "controlplane": {
            "sqlite_path": "controlplane/cp.db",
            "openminion_enabled": False,
        },
        "telegram": {
            "enabled": "telegram" in channels,
            "botToken": "telegram-token",
            "mode": "polling",
            "polling": {"stateSqlitePath": "controlplane/telegram-poll-state.db"},
        },
        "slack": {
            "enabled": "slack" in channels,
            "botToken": "xoxb-test-token",
            "signingSecret": "slack-signing-secret",
            "mode": "http",
            "stateSqlitePath": "controlplane/slack-state.db",
        },
    }
    return config


def test_telegram_and_slack_share_one_controlplane_runtime(tmp_path: Path) -> None:
    registry, components = build_channel_registry(
        config=_config(tmp_path, channels=["telegram", "slack"]),
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
        logger=__import__("logging").getLogger("test"),
    )

    assert components is not None
    telegram = registry.get("telegram")
    slack = registry.get("slack")

    assert isinstance(telegram, TelegramPollingRunner)
    assert isinstance(slack, SlackHttpEventsRunner)
    assert telegram._runtime is components.dispatcher
    assert slack._runtime is components.dispatcher
    assert telegram._store is components.store
    assert slack._store is components.store
    assert components.inbox_worker.store is components.store
    assert components.inbox_worker.dispatcher is components.dispatcher
    assert components.inbox_worker.authorizer is not None
    assert components.inbox_worker.authorizer.store is components.store
    assert components.inbox_worker.rate_limiter is components.rate_limiter
    assert components.inbox_worker.audit_logger is components.audit_logger
    assert telegram._outbox_worker is components.outbox_worker
    assert slack._outbox_worker is components.outbox_worker
    assert registry.names() == ["console", "slack", "telegram"]

    components.close()


def test_lifecycle_exposes_channel_supervisor_for_controlplane_channels(
    tmp_path: Path,
) -> None:
    lifecycle = LifecycleService.from_config(
        _config(tmp_path, channels=["telegram"]),
        config_path=str(tmp_path / "config.json"),
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
    )
    runtime = lifecycle.build(
        security_policy=SecurityPolicyEngine(),
        load_tool_plugins=False,
    )
    try:
        assert runtime.controlplane_components is not None
        assert runtime.channel_supervisor is not None
        assert (
            runtime.channel_supervisor._inbox_worker
            is runtime.controlplane_components.inbox_worker
        )
        status = lifecycle.status_payload(runtime)["channel_runtime"]
        assert status["state"] == "stopped"
        assert status["inbox_worker_alive"] is False
        assert sorted(status["channels"]) == ["telegram"]
    finally:
        runtime.controlplane_components.close()
