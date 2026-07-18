from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from openminion.modules.controlplane.channels.telegram.config import (
    AccessConfig,
    ActionsConfig,
    PairingConfig,
    PollingConfig,
    TelegramChannelConfig,
)
from openminion.modules.controlplane.channels.telegram.delivery import (
    TelegramDeliveryService,
)
from openminion.modules.controlplane.channels.telegram.polling import (
    TelegramPollingRunner,
)
from openminion.modules.controlplane.channels.telegram.storage.store import (
    TelegramPollStateStore,
)


class _FakeAPI:
    def __init__(self) -> None:
        self.delete_webhook_calls = 0
        self.get_updates_calls = 0

    def get_me(self) -> dict[str, Any]:
        return {"id": "123", "username": "leasebot"}

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> dict[str, Any]:
        self.delete_webhook_calls += 1
        return {"ok": True}

    def get_updates(self, **_kwargs: Any) -> list[dict[str, Any]]:
        self.get_updates_calls += 1
        return []

    def send_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"message_id": 1, "chat": {"id": payload.get("chat_id")}}


class _Runtime:
    def handle_inbound(self, inbound: Any) -> dict[str, Any]:
        return {"type": "chat", "text": ""}


def _config() -> TelegramChannelConfig:
    return TelegramChannelConfig(
        enabled=True,
        bot_token="token",
        mode="polling",
        polling=PollingConfig(drop_pending_on_start=False, persist_offset=True),
        access=AccessConfig(dm_policy="allowlist", allow_from_user_ids=[]),
        pairing=PairingConfig(enabled=False, mode="off"),
        actions=ActionsConfig(send_message=False),
    )


def _runner(api: _FakeAPI, store: TelegramPollStateStore) -> TelegramPollingRunner:
    cfg = _config()
    return TelegramPollingRunner(
        config=cfg,
        api=api,  # type: ignore[arg-type]
        runtime=_Runtime(),
        delivery=TelegramDeliveryService(
            api=api,  # type: ignore[arg-type]
            delivery_config=cfg.delivery,
            reply_config=cfg.reply,
        ),
        state_store=store,
        sleep_fn=lambda _seconds: None,
    )


def test_polling_lease_blocks_second_local_consumer(tmp_path: Path) -> None:
    store = TelegramPollStateStore(tmp_path / "poll.db")
    _insert_live_foreign_lease(store, account_id="telegram-bot:123")
    api = _FakeAPI()

    with pytest.raises(RuntimeError, match="already owned locally"):
        _runner(api, store).run_once()

    assert api.delete_webhook_calls == 0
    assert api.get_updates_calls == 0


def test_polling_runner_releases_lease_on_stop(tmp_path: Path) -> None:
    store = TelegramPollStateStore(tmp_path / "poll.db")
    runner = _runner(_FakeAPI(), store)

    runner.run_once()
    assert _lease_count(store, account_id="telegram-bot:123") == 1

    runner.stop()
    assert _lease_count(store, account_id="telegram-bot:123") == 0


def _lease_count(store: TelegramPollStateStore, *, account_id: str) -> int:
    with sqlite3.connect(store.sqlite_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM telegram_polling_leases WHERE account_id = ?",
            (account_id,),
        ).fetchone()
    return int(row[0])


def _insert_live_foreign_lease(
    store: TelegramPollStateStore,
    *,
    account_id: str,
) -> None:
    now = int(time.time())
    with sqlite3.connect(store.sqlite_path) as conn:
        conn.execute(
            """
            INSERT INTO telegram_polling_leases(
                account_id,
                owner_pid,
                process_start_marker,
                command,
                acquired_at_ts,
                heartbeat_at_ts
            ) VALUES (?,?,?,?,?,?)
            """,
            (account_id, 1, "foreign-process", "other runner", now, now),
        )
