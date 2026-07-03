from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.controlplane.interfaces import (
    CONTROLPLANE_INTERFACE_VERSION,
)
from openminion.modules.controlplane.runtime.channels import ChannelRegistry
from openminion.modules.controlplane.runtime.worker.outbox import OutboxWorker
from openminion.modules.controlplane.storage.sqlite import (
    SQLiteControlPlaneStore,
)


class _FailingAdapter:
    contract_version = CONTROLPLANE_INTERFACE_VERSION
    channel_id = "telegram"

    def __init__(self) -> None:
        self.calls = 0

    def start(self, stop_event=None) -> None:  # pragma: no cover - not used
        del stop_event

    def deliver(self, payload, ctx):  # noqa: ANN001
        del payload, ctx
        self.calls += 1
        raise RuntimeError("network down")


def _force_due(store: SQLiteControlPlaneStore, outbox_id: str) -> None:
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE cp_outbox SET next_attempt_at = ? WHERE outbox_id = ?",
            ("1970-01-01T00:00:00+00:00", outbox_id),
        )


@pytest.mark.parametrize("max_attempts", [1, 2, 3, 8])
def test_outbox_dead_letters_on_nth_failure(tmp_path: Path, max_attempts: int) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    try:
        outbox_id = store.enqueue_outbox(
            channel="telegram",
            chat_id="100",
            payload={"text": "hello"},
        )
        adapter = _FailingAdapter()
        registry = ChannelRegistry()
        registry.register(adapter)
        worker = OutboxWorker(
            store=store,
            registry=registry,
            max_attempts=max_attempts,
            max_backoff_s=1,
        )

        last_status: str | None = None
        for i in range(max_attempts):
            _force_due(store, outbox_id)
            result = worker.run_once()
            assert result is not None, f"run_once {i + 1} returned None"
            last_status = str(result["status"])
            if i < max_attempts - 1:
                assert last_status in {"retry", "failed"}, (
                    f"max_attempts={max_attempts} attempt {i + 1}: "
                    f"expected retry/failed, got {last_status}"
                )

        assert last_status == "dead", (
            f"max_attempts={max_attempts}: expected status=dead on attempt "
            f"{max_attempts}, got {last_status}"
        )
        assert adapter.calls == max_attempts

        final = store.get_outbox(outbox_id)
        assert final is not None
        assert final["status"] == "dead"
        assert int(final["attempts"]) == max_attempts

        _force_due(store, outbox_id)
        post_dead = worker.run_once()
        assert post_dead is None
        assert adapter.calls == max_attempts
    finally:
        store.close()
