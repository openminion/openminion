from __future__ import annotations

from pathlib import Path

from openminion.modules.controlplane.channels.telegram.webhook import (
    TelegramWebhookRunner,
)
from openminion.services.runtime.lifecycle import LifecycleService
from openminion.services.security.policy import SecurityPolicyEngine

from tests.controlplane.telegram.integration.fixtures import drain_outbox
from tests.controlplane.telegram.integration.transports import (
    DeterministicTelegramTransport,
)
from tests.integration.test_unified_config_bootstrap import (
    _close_runtime,
    _make_config,
)


WEBHOOK_SECRET = "unified-config-webhook-secret-42"


def _build_webhook_runtime(tmp_path: Path):
    config = _make_config(tmp_path, mode="webhook")
    telegram = config.channels["telegram"]
    telegram["access"] = {
        "dmPolicy": "allowlist",
        "allowFromUserIds": [456],
        "groupPolicy": "deny",
    }
    telegram["pairing"] = {"enabled": False, "mode": "off"}
    # _make_config does not supply a webhook block; the LifecycleService
    # pipeline tolerates that for registration tests, but CPE-13 explicitly
    # needs a webhook secret to exercise the auth path.
    telegram["webhook"] = {
        "enabled": True,
        "url": "https://example.test/webhook",
        "secret": WEBHOOK_SECRET,
        "dropPendingUpdates": True,
    }

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
    return runtime


def _make_update(text: str = "hello unified webhook", update_id: int = 1) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 100,
            "from": {"id": 456, "is_bot": False, "first_name": "Test"},
            "chat": {"id": 456, "type": "private"},
            "date": 1_700_000_000,
            "text": text,
        },
    }


def test_unified_config_webhook_runner_dispatches_inbound_to_outbound(
    tmp_path: Path,
) -> None:
    runtime = _build_webhook_runtime(tmp_path)
    try:
        runner = runtime.channels.get("telegram")
        assert isinstance(runner, TelegramWebhookRunner)

        transport = DeterministicTelegramTransport(bot_token="token")
        assert hasattr(runner, "_api")
        assert hasattr(runner, "_delivery")
        assert hasattr(runner._delivery, "_api")
        runner._api = transport.api
        runner._delivery._api = transport.api

        # initialize() fetches bot info via _api.get_me() — the mock returns
        # a stub bot record, so this should not raise.
        runner.initialize()

        result = runner.handle_webhook_update(
            _make_update(text="hello unified webhook", update_id=1),
            secret_token=WEBHOOK_SECRET,
        )

        assert result["success"] is True
        assert result.get("update_id") == 1
        # drive the lifecycle-attached worker to flush the outbox.
        outbox_worker = getattr(runner, "_outbox_worker", None)
        assert outbox_worker is not None, (
            "lifecycle did not wire outbox worker into telegram webhook runner"
        )
        drain_outbox(outbox_worker)
        assert transport.get_outbound_texts() == [
            "[agent:default] hello unified webhook"
        ]
    finally:
        _close_runtime(runtime)


def test_unified_config_webhook_runner_rejects_invalid_secret(
    tmp_path: Path,
) -> None:
    runtime = _build_webhook_runtime(tmp_path)
    try:
        runner = runtime.channels.get("telegram")
        assert isinstance(runner, TelegramWebhookRunner)

        transport = DeterministicTelegramTransport(bot_token="token")
        runner._api = transport.api
        runner._delivery._api = transport.api
        runner.initialize()

        # Wrong secret — webhook.py returns a structured unauthorized payload
        # rather than raising.
        bad = runner.handle_webhook_update(
            _make_update(text="nope", update_id=2),
            secret_token="not-the-right-secret",
        )
        assert bad.get("success") is False
        assert bad.get("error") == "unauthorized"
        assert bad.get("reason") == "invalid_secret_token"
        assert transport.get_outbound_texts() == []

        # Missing secret path — distinct reason code.
        missing = runner.handle_webhook_update(
            _make_update(text="still nope", update_id=3),
            secret_token=None,
        )
        assert missing.get("success") is False
        assert missing.get("error") == "unauthorized"
        assert missing.get("reason") == "missing_secret_token"
        assert transport.get_outbound_texts() == []
    finally:
        _close_runtime(runtime)
