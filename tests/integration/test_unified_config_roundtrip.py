from __future__ import annotations

from pathlib import Path

from openminion.modules.controlplane.channels.telegram.polling import (
    TelegramPollingRunner,
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


def test_unified_config_polling_runner_dispatches_inbound_to_outbound(
    tmp_path: Path,
) -> None:
    config = _make_config(tmp_path, mode="polling")
    telegram = config.channels["telegram"]
    telegram["access"] = {
        "dmPolicy": "allowlist",
        "allowFromUserIds": [456],
        "groupPolicy": "deny",
    }
    telegram["pairing"] = {"enabled": False, "mode": "off"}

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

    try:
        runner = runtime.channels.get("telegram")
        assert isinstance(runner, TelegramPollingRunner)

        transport = DeterministicTelegramTransport(bot_token="token")
        assert hasattr(runner, "_api")
        assert hasattr(runner, "_delivery")
        assert hasattr(runner._delivery, "_api")
        runner._api = transport.api
        runner._delivery._api = transport.api

        transport.inject_message(
            chat_id=123,
            user_id=456,
            text="hello unified config",
            message_id=10,
        )

        processed = runner.run_once()

        assert processed == 1
        # outbound is now async via OutboxWorker. Drive the worker
        # attached by lifecycle so the enqueued reply lands on the transport.
        outbox_worker = getattr(runner, "_outbox_worker", None)
        assert outbox_worker is not None, (
            "lifecycle did not wire outbox worker into telegram runner"
        )
        drain_outbox(outbox_worker)
        assert transport.get_outbound_texts() == [
            "[agent:default] hello unified config"
        ]
    finally:
        _close_runtime(runtime)
