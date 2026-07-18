from __future__ import annotations

from pathlib import Path
from typing import Any

from openminion.modules.controlplane.channels.telegram.polling import (
    TelegramPollingRunner,
)
from openminion.modules.controlplane.runtime.channels import (
    ChannelRegistry as ControlPlaneChannelRegistry,
)
from openminion.modules.controlplane.runtime.worker.outbox import OutboxWorker
from openminion.services.runtime.lifecycle import LifecycleService
from openminion.services.security.policy import SecurityPolicyEngine

from tests.controlplane.telegram.integration.transports import (
    DeterministicTelegramTransport,
)
from tests.integration.test_unified_config_bootstrap import (
    _close_runtime,
    _make_config,
)


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


def _audit_events(runner: Any) -> list[dict[str, Any]]:
    audit_logger = getattr(runner, "_audit_logger", None)
    if audit_logger is None:
        return []
    events = getattr(audit_logger, "events", None)
    if events is None:
        return []
    out: list[dict[str, Any]] = []
    for ev in events:
        out.append(
            {
                "event_type": getattr(ev, "event_type", None),
                "outcome": getattr(ev, "outcome", None),
                "details": dict(getattr(ev, "details", {}) or {}),
            }
        )
    return out


def test_inbound_enqueues_outbox_and_worker_drains(tmp_path: Path) -> None:
    runtime = _build_polling_runtime(tmp_path)
    try:
        runner = runtime.channels.get("telegram")
        assert isinstance(runner, TelegramPollingRunner)
        assert runner._outbox_worker is not None, (
            "lifecycle did not wire outbox worker into telegram runner"
        )
        worker = runner._outbox_worker

        transport = DeterministicTelegramTransport(bot_token="token")
        _patch_transport(runner, transport)

        transport.inject_message(
            chat_id=123,
            user_id=456,
            text="cpd-02 hello",
            message_id=1,
        )

        processed = runner.run_once()
        assert processed == 1

        store = runner._store
        rows = store._inbox_outbox._rs.query_dicts(  # type: ignore[attr-defined]
            "SELECT outbox_id, channel, chat_id, status FROM cp_outbox",
        )
        assert len(rows) == 1, rows
        outbox_row = rows[0]
        assert outbox_row["channel"] == "telegram"
        assert str(outbox_row["chat_id"]) == "123"
        assert outbox_row["status"] == "pending"

        events = _audit_events(runner)
        enqueue_events = [
            ev for ev in events if ev["event_type"] == "cp.outbox.enqueued"
        ]
        assert enqueue_events, events
        assert enqueue_events[0]["details"].get("outbox_id") == outbox_row["outbox_id"]

        before_send_count = len(transport.get_outbound_texts())
        result = worker.run_once()
        assert result is not None
        assert result.get("status") == "sent"

        outbound_after = transport.get_outbound_texts()
        assert len(outbound_after) == before_send_count + 1
        assert outbound_after[-1] == "[agent:default] cpd-02 hello"

        events_after = _audit_events(runner)
        delivery_sent = [
            ev for ev in events_after if ev["event_type"] == "cp.delivery.sent"
        ]
        assert delivery_sent, events_after

        rows_after = store._inbox_outbox._rs.query_dicts(  # type: ignore[attr-defined]
            "SELECT status FROM cp_outbox WHERE outbox_id = ?",
            (outbox_row["outbox_id"],),
        )
        assert rows_after and rows_after[0]["status"] == "sent"
    finally:
        _close_runtime(runtime)


class _RaisingDelivery:
    contract_version = "v1"

    def __init__(self) -> None:
        self.calls = 0

    def send_payload(self, payload, target):  # pragma: no cover - defensive
        return self.send_message(payload=payload, target=target)

    def send_message(self, *, payload=None, target=None, **_):  # pragma: no cover
        self.calls += 1
        raise RuntimeError("simulated delivery failure")


def test_outbox_worker_failure_path_dead_letters_after_max_attempts(
    tmp_path: Path,
) -> None:
    runtime = _build_polling_runtime(tmp_path)
    try:
        runner = runtime.channels.get("telegram")
        assert isinstance(runner, TelegramPollingRunner)
        store = runner._store
        audit_logger = runner._audit_logger

        registry = ControlPlaneChannelRegistry()
        registry.register(runner)
        worker = OutboxWorker(
            store=store,
            registry=registry,
            audit_logger=audit_logger,
            max_attempts=2,
            max_backoff_s=0,
        )

        runner._delivery = _RaisingDelivery()

        outbox_id = store.enqueue_outbox(
            channel="telegram",
            chat_id="123",
            payload={"type": "chat", "text": "doomed"},
        )

        observed_states: list[str | None] = []
        for _ in range(8):
            result = worker.run_once()
            if result is None:
                break
            observed_states.append(result.get("status"))
            if result.get("status") == "dead":
                break

        assert "dead" in observed_states, observed_states

        events = _audit_events(runner)
        failed_events = [
            ev for ev in events if ev["event_type"] == "cp.delivery.failed"
        ]
        assert len(failed_events) >= 2, events

        deadletter_events = [
            ev for ev in events if ev["event_type"] == "cp.outbox.deadletter"
        ]
        assert deadletter_events, events
        assert deadletter_events[0]["details"].get("outbox_id") == outbox_id

        rows = store._inbox_outbox._rs.query_dicts(  # type: ignore[attr-defined]
            "SELECT status FROM cp_outbox WHERE outbox_id = ?",
            (outbox_id,),
        )
        assert rows and rows[0]["status"] == "dead"
    finally:
        _close_runtime(runtime)


def test_outbox_worker_thread_joins_cleanly_on_stop(tmp_path: Path) -> None:
    runtime = _build_polling_runtime(tmp_path)
    try:
        runner = runtime.channels.get("telegram")
        assert isinstance(runner, TelegramPollingRunner)
        assert getattr(runner, "_outbox_managed_by_supervisor", False) is True

        supervisor = runtime.channel_supervisor
        assert supervisor is not None, "lifecycle did not wire channel supervisor"

        supervisor._start_outbox_worker()
        thread = supervisor._outbox_thread
        assert thread is not None and thread.is_alive()

        supervisor._stop_event.set()
        supervisor._stop_outbox_worker(timeout_seconds=2.0)
        if thread.is_alive():
            thread.join(timeout=2.0)
        assert not thread.is_alive(), "outbox worker thread did not stop in time"
    finally:
        _close_runtime(runtime)
