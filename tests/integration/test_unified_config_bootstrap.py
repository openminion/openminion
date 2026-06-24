from __future__ import annotations

from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.modules.controlplane.channels.telegram.polling import (
    TelegramPollingRunner,
)
from openminion.modules.controlplane.channels.telegram.webhook import (
    TelegramWebhookRunner,
)
from openminion.services.runtime.catalog import ExtensionCatalog
from openminion.services.runtime.lifecycle import LifecycleService
from openminion.services.security.policy import SecurityPolicyEngine
from tests._csc_fixtures import _csc_install_default_agent


def _make_config(
    tmp_path: Path,
    *,
    mode: str = "polling",
    telegram_enabled: bool = True,
    include_channels: bool = True,
    enabled_channels: list[str] | None = None,
) -> OpenMinionConfig:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.env = {
        "OPENMINION_HOME": str(tmp_path),
        "OPENMINION_DATA_ROOT": str(tmp_path / ".openminion"),
    }
    config.enabled_channels = enabled_channels or ["console", "telegram"]
    if include_channels:
        config.channels = {
            "controlplane": {
                "sqlite_path": "controlplane/cp.db",
                "openminion_enabled": False,
            },
            "telegram": {
                "enabled": telegram_enabled,
                "botToken": "token",
                "mode": mode,
                "polling": {
                    "stateSqlitePath": "controlplane/telegram-poll-state.db",
                },
            },
        }
    return config


def _build_runtime(config: OpenMinionConfig, tmp_path: Path):
    lifecycle = LifecycleService.from_config(
        config,
        config_path=str(tmp_path / "config.json"),
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
    )
    runtime = lifecycle.build(
        security_policy=SecurityPolicyEngine(),
        load_tool_plugins=False,
    )
    return lifecycle, runtime


def _close_runtime(runtime) -> None:
    for name in runtime.channels.names():
        channel = runtime.channels.get(name)
        state_store = getattr(channel, "_state_store", None)
        if callable(getattr(state_store, "close", None)):
            state_store.close()
        cp_runtime = getattr(channel, "_runtime", None)
        store = getattr(cp_runtime, "store", None)
        if callable(getattr(store, "close", None)):
            store.close()
        brain = getattr(cp_runtime, "brain_client", None)
        if callable(getattr(brain, "close", None)):
            brain.close()


def test_unified_config_registers_polling_telegram_adapter(tmp_path: Path) -> None:
    config = _make_config(tmp_path, mode="polling")
    lifecycle, runtime = _build_runtime(config, tmp_path)
    try:
        assert runtime.channels.names() == ["console", "telegram"]
        assert isinstance(runtime.channels.get("telegram"), TelegramPollingRunner)
        assert [record.name for record in runtime.catalog.channels] == [
            "console",
            "telegram",
        ]
        assert lifecycle.status_payload(runtime)["channels"][1]["name"] == "telegram"
    finally:
        _close_runtime(runtime)


def test_unified_config_registers_webhook_telegram_adapter(tmp_path: Path) -> None:
    config = _make_config(tmp_path, mode="webhook")
    _, runtime = _build_runtime(config, tmp_path)
    try:
        assert isinstance(runtime.channels.get("telegram"), TelegramWebhookRunner)
    finally:
        _close_runtime(runtime)


def test_enabled_channels_console_only_keeps_console_only(tmp_path: Path) -> None:
    config = _make_config(
        tmp_path,
        enabled_channels=["console"],
        include_channels=False,
    )
    _, runtime = _build_runtime(config, tmp_path)
    try:
        assert runtime.channels.names() == ["console"]
        assert [record.name for record in runtime.catalog.channels] == ["console"]
    finally:
        _close_runtime(runtime)


def test_enabled_channels_is_authoritative_over_nested_telegram_enabled(
    tmp_path: Path,
) -> None:
    config = _make_config(tmp_path, mode="polling", telegram_enabled=False)
    _, runtime = _build_runtime(config, tmp_path)
    try:
        assert runtime.channels.names() == ["console", "telegram"]
        assert isinstance(runtime.channels.get("telegram"), TelegramPollingRunner)
    finally:
        _close_runtime(runtime)


def test_legacy_module_defaults_still_build_when_channels_absent(
    tmp_path: Path,
) -> None:
    config = _make_config(
        tmp_path,
        include_channels=False,
        enabled_channels=["console", "telegram"],
    )
    config.runtime.env["TELEGRAM_BOT_TOKEN"] = "legacy-token"
    _, runtime = _build_runtime(config, tmp_path)
    try:
        assert runtime.channels.names() == ["console", "telegram"]
        assert isinstance(runtime.channels.get("telegram"), TelegramPollingRunner)
    finally:
        _close_runtime(runtime)


def test_status_payload_includes_unified_telegram_channel(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    lifecycle, runtime = _build_runtime(config, tmp_path)
    try:
        payload = lifecycle.status_payload(runtime)
        channel_names = [item["name"] for item in payload["channels"]]
        assert channel_names == ["console", "telegram"]
    finally:
        _close_runtime(runtime)


def test_extension_catalog_channel_records_are_side_effect_free(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    cp_db = tmp_path / ".openminion" / "controlplane" / "cp.db"
    tg_db = tmp_path / ".openminion" / "controlplane" / "telegram-poll-state.db"

    catalog = ExtensionCatalog.from_config(config)

    assert [record.name for record in catalog.channels] == ["console", "telegram"]
    assert not cp_db.exists()
    assert not tg_db.exists()
