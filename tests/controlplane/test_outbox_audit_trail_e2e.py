from __future__ import annotations

from pathlib import Path
from typing import Any

from openminion.modules.controlplane.runtime.channels import ChannelRegistry
from openminion.modules.controlplane.interfaces import CONTROLPLANE_INTERFACE_VERSION
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore
from openminion.modules.controlplane.runtime.worker.outbox import OutboxWorker


class _AuditCollector:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(
        self, event_type: str, *, details: dict[str, object], **kwargs: object
    ) -> None:
        payload = dict(details)
        payload.update(kwargs)
        self.events.append((event_type, payload))


class _FailingAdapter:
    contract_version = CONTROLPLANE_INTERFACE_VERSION
    channel_id = "telegram"

    def __init__(self) -> None:
        self.deliver_calls = 0

    def start(self, stop_event=None) -> None:  # pragma: no cover - not used
        del stop_event

    def deliver(self, payload, ctx):  # noqa: ANN001
        del payload, ctx
        self.deliver_calls += 1
        raise RuntimeError("network down")


def _unblock_next_attempt(store: SQLiteControlPlaneStore, outbox_id: str) -> None:
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE cp_outbox SET next_attempt_at = ? WHERE outbox_id = ?",
            ("1970-01-01T00:00:00+00:00", outbox_id),
        )


def test_outbox_audit_trail_retry_then_dead_letter(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    audit = _AuditCollector()

    outbox_id = store.enqueue_outbox(
        channel="telegram",
        chat_id="100",
        payload={"text": "hello, will fail"},
    )

    registry = ChannelRegistry()
    adapter = _FailingAdapter()
    registry.register(adapter)

    worker = OutboxWorker(
        store=store,
        registry=registry,
        audit_logger=audit,
        max_attempts=3,
        max_backoff_s=1,
    )

    statuses: list[dict[str, Any]] = []
    for _ in range(3):
        result = worker.run_once()
        assert result is not None
        statuses.append(result)
        _unblock_next_attempt(store, outbox_id)

    assert statuses[0]["status"] == "retry"
    assert statuses[1]["status"] == "retry"
    assert statuses[2]["status"] == "dead"

    assert adapter.deliver_calls == 3

    event_names = [ev for ev, _ in audit.events]

    failed_indices = [
        i for i, name in enumerate(event_names) if name == "cp.delivery.failed"
    ]
    assert len(failed_indices) == 3, (
        f"expected 3 cp.delivery.failed events, got {event_names}"
    )
    assert failed_indices == sorted(failed_indices)

    deadletter_indices = [
        i for i, name in enumerate(event_names) if name == "cp.outbox.deadletter"
    ]
    assert len(deadletter_indices) == 1, (
        f"expected 1 cp.outbox.deadletter event, got {event_names}"
    )
    assert deadletter_indices[0] > failed_indices[-1]

    deadletter_event = audit.events[deadletter_indices[0]]
    assert deadletter_event[1].get("reason") == "max_attempts_exceeded"

    assert "cp.route.outbox.selected" not in event_names, (
        "cp.route.outbox.selected should not fire on failure-only runs"
    )

    final_row = store.get_outbox(outbox_id)
    assert final_row is not None
    assert final_row["status"] == "dead", (
        f"expected terminal status='dead', got {final_row['status']}"
    )
    assert int(final_row["attempts"]) == 3
    assert final_row["last_error"] == "network down"

    store.close()
