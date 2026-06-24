from __future__ import annotations

from pathlib import Path

from openminion.modules.controlplane.channels.telegram.state import (
    TelegramPollStateStore,
)


def test_state_store_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "poll.db"
    store = TelegramPollStateStore(str(db))

    assert store.get_last_update_id("bot-1") == 0
    store.set_last_update_id("bot-1", 123)
    assert store.get_last_update_id("bot-1") == 123

    store.close()

    store2 = TelegramPollStateStore(str(db))
    assert store2.get_last_update_id("bot-1") == 123
    store2.close()


def test_state_store_has_no_pending_clarify_accessors(tmp_path: Path) -> None:
    db = tmp_path / "poll.db"
    store = TelegramPollStateStore(str(db))
    assert not hasattr(store, "set_pending_clarify")
    assert not hasattr(store, "get_pending_clarify")
    assert not hasattr(store, "clear_pending_clarify")
    store.close()
