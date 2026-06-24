from __future__ import annotations

import threading
from unittest.mock import MagicMock

from openminion.modules.controlplane.channels.telegram.config import (
    ClarifyConfig,
    WebhookConfig,
)
from openminion.modules.controlplane.channels.telegram.webhook import (
    TelegramWebhookRunner,
)


class _MockConfig:
    def __init__(self) -> None:
        self.webhook = WebhookConfig(enabled=False, secret=None)
        self.clarify = ClarifyConfig()
        self.pairing = MagicMock()
        self.pairing.auto_send_pairing_hint = False
        self.access = MagicMock()
        self.actions = MagicMock()
        self.actions.send_message = True


def _build_runner() -> TelegramWebhookRunner:
    api = MagicMock()
    api.get_me.return_value = {"id": "123456789", "username": "testbot"}
    runtime = MagicMock()
    delivery = MagicMock()
    state_store = MagicMock()
    runner = TelegramWebhookRunner(
        config=_MockConfig(),
        api=api,
        runtime=runtime,
        delivery=delivery,
        state_store=state_store,
    )
    runner.initialize()
    return runner


def _update(update_id: int) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "chat": {"id": 111, "type": "private"},
            "from": {"id": 111, "first_name": "Test"},
            "text": "hello",
        },
    }


def test_sequential_dedup() -> None:
    runner = _build_runner()
    first = runner.handle_webhook_update(_update(123))
    second = runner.handle_webhook_update(_update(123))

    assert first["success"] is True
    assert first.get("duplicate", False) is False
    assert second["success"] is True
    assert second.get("duplicate", False) is True
    assert second["update_id"] == 123


def test_concurrent_dedup_under_race() -> None:
    for iteration in range(50):
        runner = _build_runner()
        update_id = 1_000_000 + iteration
        barrier = threading.Barrier(2)
        results: list[dict[str, object]] = []
        results_lock = threading.Lock()

        def call() -> None:
            barrier.wait()
            result = runner.handle_webhook_update(_update(update_id))
            with results_lock:
                results.append(result)

        t1 = threading.Thread(target=call)
        t2 = threading.Thread(target=call)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(results) == 2
        duplicates = sum(1 for r in results if r.get("duplicate"))
        accepted = sum(
            1 for r in results if r.get("success") and not r.get("duplicate", False)
        )
        assert accepted == 1, (
            f"iteration {iteration}: expected exactly one accepted, got {results}"
        )
        assert duplicates == 1, (
            f"iteration {iteration}: expected exactly one deduped, got {results}"
        )


def test_fifo_eviction_under_concurrency() -> None:
    runner = _build_runner()
    first_id = 0
    runner.handle_webhook_update(_update(first_id))

    def insert_range(start: int, count: int) -> None:
        for i in range(start, start + count):
            runner.handle_webhook_update(_update(i))

    threads = [
        threading.Thread(target=insert_range, args=(1, 500)),
        threading.Thread(target=insert_range, args=(501, 500)),
        threading.Thread(target=insert_range, args=(1001, 500)),
        threading.Thread(target=insert_range, args=(1501, 500)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(runner._recent_update_ids) <= 1000
    assert first_id not in runner._recent_update_ids
