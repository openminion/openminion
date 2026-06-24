from __future__ import annotations

import threading
from typing import Any

from tests.controlplane.telegram.integration.fixtures import (
    ControlplaneRuntimeFixture,
)


def _make_update(
    *, user_id: int, chat_id: int, update_id: int, text: str
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id * 10,
            "from": {"id": user_id, "is_bot": False, "first_name": f"User{user_id}"},
            "chat": {"id": chat_id, "type": "private"},
            "date": 1234567890,
            "text": text,
        },
    }


def test_concurrent_inbound_four_threads() -> None:
    with ControlplaneRuntimeFixture() as fixture:
        n_workers = 4
        barrier = threading.Barrier(n_workers)
        results: list[dict[str, Any]] = []
        results_lock = threading.Lock()
        exceptions: list[BaseException] = []
        exceptions_lock = threading.Lock()

        def _worker(idx: int) -> None:
            try:
                update = _make_update(
                    user_id=1000 + idx,
                    chat_id=2000 + idx,
                    update_id=idx + 1,
                    text=f"concurrent msg {idx}",
                )
                barrier.wait(timeout=5.0)  # simultaneous release
                payload = fixture.inject_update(update)
                with results_lock:
                    results.append(payload)
            except BaseException as exc:  # noqa: BLE001 - capture all
                with exceptions_lock:
                    exceptions.append(exc)

        threads = [
            threading.Thread(target=_worker, args=(i,), daemon=True)
            for i in range(n_workers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
            assert not t.is_alive(), "worker thread did not finish"

        # Fail loudly if any worker raised — do NOT paper over genuine
        # defects (per CPE-06 spec: "file bug, don't paper over").
        assert not exceptions, (
            f"worker threads raised {len(exceptions)} exception(s); "
            f"first: {type(exceptions[0]).__name__}: {exceptions[0]!r}"
        )

        # All 4 outbounds landed.
        outbounds = fixture.captured_outbounds
        assert len(outbounds) == n_workers, (
            f"expected {n_workers} outbounds, got {len(outbounds)}"
        )
        assert len(results) == n_workers

        # All session ids are unique (each user/chat got its own session
        # via InMemoryRouter's counter).
        session_ids = [p.get("session_id") for p in results]
        assert all(session_ids), f"some session_ids missing: {session_ids}"
        assert len(set(session_ids)) == n_workers, (
            f"expected {n_workers} unique session ids, got {session_ids}"
        )

        # The outbound session ids match the dispatched session ids
        # (sanity check — ensures no cross-talk between threads).
        outbound_session_ids = {o.get("session_id") for o in outbounds}
        assert outbound_session_ids == set(session_ids), (
            f"outbound session ids {outbound_session_ids} != dispatched "
            f"{set(session_ids)}"
        )
