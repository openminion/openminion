from __future__ import annotations

import json
from pathlib import Path

import pytest

from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore
from openminion.modules.controlplane.channels.telegram.cli import (
    build_channel_registry,
    build_runtime,
    main,
)
from openminion.modules.controlplane.channels.telegram.polling import (
    TelegramPollingRunner,
)
from openminion.modules.controlplane.channels.telegram.webhook import (
    TelegramWebhookRunner,
)


def test_build_runtime_uses_sqlite_controlplane_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_MODULE_STANDALONE", "1")
    config_path = tmp_path / "telegram-runtime.json"
    db_path = tmp_path / "cp.db"
    config_path.write_text(
        json.dumps(
            {
                "controlplane": {
                    "sqlite_path": str(db_path),
                    "wal": True,
                    "openminion_enabled": False,
                }
            }
        ),
        encoding="utf-8",
    )

    runtime = build_runtime(str(config_path))
    try:
        assert isinstance(runtime.store, SQLiteControlPlaneStore)
        assert db_path.exists()
    finally:
        runtime.store.close()


def _write_telegram_runtime_config(tmp_path: Path, *, mode: str) -> Path:
    config_path = tmp_path / f"telegram-runtime-{mode}.json"
    config_path.write_text(
        json.dumps(
            {
                "controlplane": {
                    "sqlite_path": str(tmp_path / "cp.db"),
                    "wal": True,
                    "openminion_enabled": False,
                },
                "channels": {
                    "telegram": {
                        "enabled": True,
                        "botToken": "token",
                        "mode": mode,
                        "polling": {"stateSqlitePath": str(tmp_path / "poll-state.db")},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return config_path


def _close_registry_runner(registry) -> None:
    adapter = registry.get("telegram")
    runtime = getattr(adapter, "_runtime", None)
    runtime_store = getattr(runtime, "store", None)
    if callable(getattr(runtime_store, "close", None)):
        runtime_store.close()
    state_store = getattr(adapter, "_state_store", None)
    if callable(getattr(state_store, "close", None)):
        state_store.close()


@pytest.mark.parametrize(
    ("mode", "runner_cls"),
    [
        ("polling", TelegramPollingRunner),
        ("webhook", TelegramWebhookRunner),
    ],
)
def test_build_channel_registry_registers_expected_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    runner_cls: type[TelegramPollingRunner] | type[TelegramWebhookRunner],
) -> None:
    monkeypatch.setenv("OPENMINION_MODULE_STANDALONE", "1")
    config_path = _write_telegram_runtime_config(tmp_path, mode=mode)
    registry, channel_id = build_channel_registry(str(config_path))
    try:
        assert channel_id == "telegram"
        assert registry.list() == ["telegram"]
        assert isinstance(registry.get("telegram"), runner_cls)
    finally:
        _close_registry_runner(registry)


def test_main_run_path_uses_registry_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeRegistry:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False

        def get(self, channel_id: str):
            assert channel_id == "telegram"
            return object()

        def start_all(self, stop_event=None):
            assert stop_event is not None
            self.started = True
            return {"telegram": {"ok": True}}

        def stop_all(self):
            self.stopped = True
            return {"telegram": {"ok": True}}

    fake = _FakeRegistry()

    monkeypatch.setattr(
        "openminion.modules.controlplane.channels.telegram.cli.build_channel_registry",
        lambda _config: (fake, "telegram"),
    )

    rc = main(["run"])
    assert rc == 0
    assert fake.started is True
    assert fake.stopped is True


def test_pair_create_prints_compat_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _FakeAPI:
        def __init__(self, _token: str) -> None:
            pass

        def get_me(self) -> dict[str, str]:
            return {"username": "compat_bot"}

    monkeypatch.setenv("OPENMINION_MODULE_STANDALONE", "1")
    monkeypatch.setattr(
        "openminion.modules.controlplane.channels.telegram.cli.TelegramBotAPI",
        _FakeAPI,
    )
    config_path = _write_telegram_runtime_config(tmp_path, mode="polling")

    rc = main(
        [
            "pair-create",
            "--config",
            str(config_path),
            "--user-id",
            "11",
            "--chat-id",
            "22",
            "--token",
            "fixedToken123",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "PAIR_TOKEN=fixedToken123" in output
    assert "PAIR_TOKEN_HINT=" in output
    assert "PAIR_TOKEN_HASH_PREFIX=" in output
    assert "PAIR_EXPIRES_AT=" in output
    assert "PAIR_SCOPES=" in output
    assert "PAIR_DEEP_LINK=https://t.me/compat_bot?start=fixedToken123" in output
