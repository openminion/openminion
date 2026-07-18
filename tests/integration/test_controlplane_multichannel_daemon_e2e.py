from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from openminion.base.config import OpenMinionConfig
from openminion.modules.controlplane.channels.slack.webhook import (
    SlackHttpEventsRunner,
)
from openminion.modules.controlplane.channels.telegram.polling import (
    TelegramPollingRunner,
)
from openminion.services.runtime.lifecycle import LifecycleService
from openminion.services.security.policy import SecurityPolicyEngine
from tests._csc_fixtures import _csc_install_default_agent


class _TelegramAPI:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.get_updates_calls = 0

    def get_me(self) -> dict[str, Any]:
        return {"id": 123, "username": "daemon_test_bot"}

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> dict[str, Any]:
        return {"ok": True}

    def get_updates(self, **_kwargs: Any) -> list[dict[str, Any]]:
        self.get_updates_calls += 1
        if self.updates:
            return [self.updates.pop(0)]
        time.sleep(0.01)
        return []

    def send_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.sent.append(dict(payload))
        return {"message_id": len(self.sent), "chat": {"id": payload.get("chat_id")}}


class _SlackAPI:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def chat_post_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.sent.append(dict(payload))
        return {"ok": True, "ts": f"sent-{len(self.sent)}"}


def test_multichannel_daemon_uses_one_shared_runtime_and_routes_outbound(
    tmp_path: Path,
) -> None:
    lifecycle = _lifecycle(tmp_path)
    runtime = lifecycle.build(
        security_policy=SecurityPolicyEngine(),
        load_tool_plugins=False,
    )
    telegram_api = _patch_telegram(runtime.channels.get("telegram"))
    slack_api = _patch_slack(runtime.channels.get("slack"))
    supervisor = runtime.channel_supervisor

    assert supervisor is not None
    supervisor.start()
    try:
        telegram_api.updates.append(_telegram_message(update_id=1, text="hello tg"))
        runtime.channels.get("slack").handle_http_event(_slack_message("hello slack"))

        _wait_until(lambda: bool(telegram_api.sent and slack_api.sent))
        status = lifecycle.status_payload(runtime)["channel_runtime"]

        assert status["state"] == "running"
        assert telegram_api.sent[0]["chat_id"] == 100
        assert "hello tg" in telegram_api.sent[0]["text"]
        assert slack_api.sent[0]["channel"] == "D1"
        assert "hello slack" in slack_api.sent[0]["text"]

        components = runtime.controlplane_components
        assert components is not None
        assert runtime.channels.get("telegram")._runtime is components.dispatcher
        assert runtime.channels.get("slack")._runtime is components.dispatcher
        assert runtime.channels.get("telegram")._outbox_worker is components.outbox_worker
        assert runtime.channels.get("slack")._outbox_worker is components.outbox_worker
        assert components.store.get_chat_binding("telegram:100") is not None
        assert components.store.get_chat_binding("slack:T1:channel:D1") is not None
    finally:
        supervisor.stop()


def test_multichannel_daemon_degrades_one_failed_channel_without_stopping_peer(
    tmp_path: Path,
) -> None:
    lifecycle = _lifecycle(tmp_path)
    runtime = lifecycle.build(
        security_policy=SecurityPolicyEngine(),
        load_tool_plugins=False,
    )
    telegram_api = _patch_telegram(runtime.channels.get("telegram"))
    slack = runtime.channels.get("slack")

    def fail_start(*, stop_event: Any | None = None) -> None:
        raise RuntimeError("xoxb-hidden-token startup failed")

    slack.start = fail_start
    supervisor = runtime.channel_supervisor
    assert supervisor is not None

    supervisor.start()
    try:
        _wait_until(lambda: supervisor.status().state == "degraded")
        status = supervisor.status().to_dict()

        assert status["channels"]["telegram"]["state"] == "running"
        assert status["channels"]["slack"]["state"] == "failed"
        assert status["last_error"] == "<redacted>"
        assert telegram_api.get_updates_calls > 0
    finally:
        supervisor.stop()


def _lifecycle(tmp_path: Path) -> LifecycleService:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.enabled_channels = ["console", "telegram", "slack"]
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
            "enabled": True,
            "botToken": "telegram-token",
            "mode": "polling",
            "polling": {
                "timeoutSeconds": 0,
                "stateSqlitePath": "controlplane/telegram-poll-state.db",
            },
            "access": {"dmPolicy": "allow", "allowFromUserIds": [100]},
            "pairing": {"enabled": False, "mode": "off"},
        },
        "slack": {
            "enabled": True,
            "botToken": "xoxb-test-token",
            "signingSecret": "slack-signing-secret",
            "mode": "http",
            "stateSqlitePath": "controlplane/slack-state.db",
            "access": {"requirePairing": False},
        },
    }
    return LifecycleService.from_config(
        config,
        config_path=str(tmp_path / "config.json"),
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
    )


def _patch_telegram(runner: TelegramPollingRunner) -> _TelegramAPI:
    api = _TelegramAPI()
    runner._api = api  # noqa: SLF001
    runner._delivery._api = api  # noqa: SLF001
    return api


def _patch_slack(runner: SlackHttpEventsRunner) -> _SlackAPI:
    api = _SlackAPI()
    runner._delivery._api = api  # noqa: SLF001
    return api


def _telegram_message(*, update_id: int, text: str) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": 100, "username": "tester", "first_name": "Test"},
            "chat": {"id": 100, "type": "private"},
            "text": text,
        },
    }


def _slack_message(text: str) -> dict[str, Any]:
    return {
        "type": "event_callback",
        "team_id": "T1",
        "event_id": f"Ev-{time.time_ns()}",
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D1",
            "user": "U1",
            "text": text,
            "ts": "123.456",
        },
    }


def _wait_until(predicate, *, timeout_seconds: float = 2.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")
