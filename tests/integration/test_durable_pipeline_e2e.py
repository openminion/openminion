from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import dataclasses

from openminion.modules.controlplane.channels.telegram.bot_api import (
    TelegramAPIError,
)
from openminion.modules.controlplane.channels.telegram.config import RetryConfig
from openminion.modules.controlplane.channels.telegram.polling import (
    TelegramPollingRunner,
)
from openminion.services.runtime.lifecycle import LifecycleService
from openminion.services.security.policy import SecurityPolicyEngine

from tests.controlplane.telegram.integration.transports import (
    DeterministicTelegramTransport,
    MockTelegramBotAPI,
)
from tests.integration.test_unified_config_bootstrap import (
    _close_runtime,
    _make_config,
)


class _TransientFailureBotAPI(MockTelegramBotAPI):
    def __init__(self, bot_token: str = "test-bot-token") -> None:
        super().__init__(bot_token=bot_token)
        self.send_message_calls = 0

    def send_message(self, *args: Any, **kwargs: Any) -> dict[str, Any]:  # type: ignore[override]
        self.send_message_calls += 1
        if self.send_message_calls == 1:
            raise TelegramAPIError(
                code=429,
                description="Too Many Requests",
                retry_after=0,
            )
        return super().send_message(*args, **kwargs)


class _TransientFailureTransport(DeterministicTelegramTransport):
    def __init__(self, bot_token: str = "test-bot-token") -> None:
        # Skip parent ``__init__`` because we want our own _api instance.
        self._api = _TransientFailureBotAPI(bot_token=bot_token)
        self._lock = threading.Lock()


def _build_polling_runtime(tmp_path: Path):
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
    return runtime


def _patch_transport(runner: Any, transport: DeterministicTelegramTransport) -> None:
    runner._api = transport.api
    runner._delivery._api = transport.api


def _audit_event_types(audit_logger: Any) -> list[str]:
    return [getattr(ev, "event_type", None) for ev in audit_logger.events]


def _events_for_outbox(audit_logger: Any, outbox_id: str) -> list[Any]:
    return [
        ev
        for ev in audit_logger.events
        if (ev.details or {}).get("outbox_id") == outbox_id
    ]


def test_durable_pipeline_full_flow(tmp_path: Path) -> None:
    runtime = _build_polling_runtime(tmp_path)
    runner: TelegramPollingRunner | None = None
    try:
        runner = runtime.channels.get("telegram")
        assert isinstance(runner, TelegramPollingRunner)

        worker = runner._outbox_worker
        assert worker is not None, (
            "lifecycle did not wire outbox worker into telegram runner"
        )
        worker.max_backoff_s = 0

        runner._delivery._delivery = dataclasses.replace(
            runner._delivery._delivery,
            retry=RetryConfig(max_attempts=1, backoff_ms=[]),
        )

        store = runner._store
        audit_logger = runner._audit_logger
        assert store is not None
        assert audit_logger is not None

        transport = _TransientFailureTransport(bot_token="token")
        _patch_transport(runner, transport)

        update_id = transport.inject_message(
            chat_id=123,
            user_id=456,
            text="cpd-e2e hello",
            message_id=42,
        )

        processed = runner.run_once()
        assert processed == 1

        assert transport.get_outbound_texts() == [], (
            "outbound was delivered synchronously from handle_inbound; "
            "CPD-02 cutover did not take effect"
        )

        rows = store._inbox_outbox._rs.query_dicts(  # type: ignore[attr-defined]
            "SELECT outbox_id, channel, chat_id, status, attempts, "
            "payload_json FROM cp_outbox",
        )
        assert len(rows) == 1, rows
        outbox_row = rows[0]
        outbox_id = str(outbox_row["outbox_id"])
        assert outbox_row["channel"] == "telegram"
        assert str(outbox_row["chat_id"]) == "123"
        assert outbox_row["status"] == "pending"
        assert outbox_row["attempts"] == 0
        assert "cpd-e2e hello" in str(outbox_row["payload_json"])

        enqueue_events = [
            ev for ev in audit_logger.events if ev.event_type == "cp.outbox.enqueued"
        ]
        assert enqueue_events, _audit_event_types(audit_logger)
        enqueue_details = enqueue_events[-1].details or {}
        assert enqueue_details.get("outbox_id") == outbox_id
        assert int(enqueue_details.get("update_id") or -1) == update_id
        assert str(enqueue_details.get("chat_id")) == "123"

        events_before_drain1 = len(audit_logger.events)
        first_result = worker.run_once()
        assert first_result is not None
        assert first_result.get("status") == "retry", first_result
        assert first_result.get("outbox_id") == outbox_id

        assert transport.api.send_message_calls == 1
        assert transport.get_outbound_texts() == []

        retry_row = store.get_outbox(outbox_id)
        assert retry_row is not None
        assert retry_row["status"] == "failed"
        assert int(retry_row["attempts"]) == 1
        assert retry_row["next_attempt_at"], retry_row

        events_after_drain1 = audit_logger.events[events_before_drain1:]
        worker_failed = [
            ev
            for ev in events_after_drain1
            if ev.event_type == "cp.delivery.failed"
            and (ev.details or {}).get("outbox_id") == outbox_id
        ]
        assert worker_failed, [ev.event_type for ev in events_after_drain1]

        events_before_drain2 = len(audit_logger.events)
        second_result = worker.run_once()
        assert second_result is not None
        assert second_result.get("status") == "sent", second_result
        assert second_result.get("outbox_id") == outbox_id

        assert transport.api.send_message_calls == 2
        outbound_texts = transport.get_outbound_texts()
        assert outbound_texts, outbound_texts
        assert "cpd-e2e hello" in outbound_texts[-1]

        sent_row = store.get_outbox(outbox_id)
        assert sent_row is not None
        assert sent_row["status"] == "sent"

        events_after_drain2 = audit_logger.events[events_before_drain2:]
        types_after_drain2 = [ev.event_type for ev in events_after_drain2]
        assert "cp.delivery.sent" in types_after_drain2
        assert "channel.message.sent" in types_after_drain2
        for required in ("cp.delivery.sent", "channel.message.sent"):
            matched = [
                ev
                for ev in events_after_drain2
                if ev.event_type == required
                and (ev.details or {}).get("outbox_id") == outbox_id
            ]
            assert matched, (required, types_after_drain2)

        outbox_chain = _events_for_outbox(audit_logger, outbox_id)
        chain_types = [ev.event_type for ev in outbox_chain]
        assert "cp.outbox.enqueued" in chain_types
        assert "cp.delivery.failed" in chain_types
        assert "cp.delivery.sent" in chain_types
        first_enqueued = chain_types.index("cp.outbox.enqueued")
        first_failed = chain_types.index("cp.delivery.failed")
        first_sent = chain_types.index("cp.delivery.sent")
        assert first_enqueued < first_failed < first_sent, chain_types

        stop_event = threading.Event()
        runner._start_outbox_worker(stop_event)
        thread = runner._outbox_thread
        assert thread is not None and thread.is_alive()

        time.sleep(0.05)
        assert thread.is_alive()

        stop_event.set()
        join_started = time.monotonic()
        runner.stop()
        join_elapsed = time.monotonic() - join_started
        assert join_elapsed < 5.0, (
            f"runner.stop() took {join_elapsed:.2f}s — exceeded 5s budget"
        )
        # ``_stop_outbox_worker`` clears the attribute on success.
        assert runner._outbox_thread is None
        # Thread is no longer alive.
        assert not thread.is_alive()
    finally:
        # _close_runtime closes the store + brain client; calling
        # runner.stop() twice is a no-op (thread is already None).
        _close_runtime(runtime)
