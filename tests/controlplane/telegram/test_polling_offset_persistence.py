from __future__ import annotations

from pathlib import Path
from typing import Any

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


class _StubAPI:
    def get_me(self) -> dict[str, Any]:
        return {"id": "1", "username": "stubbot"}

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> dict[str, Any]:
        return {"ok": True}

    def get_updates(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    def send_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"message_id": 1, "chat": {"id": payload.get("chat_id")}}

    def edit_message_text(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload

    def answer_callback_query(self, callback_query_id: str) -> dict[str, Any]:
        return {"ok": True}

    def set_message_reaction(self, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": True}


class _StubRuntime:
    def handle_inbound(self, inbound: Any) -> dict[str, Any]:
        return {"type": "chat", "text": ""}


def _make_config(*, persist_offset: bool) -> TelegramChannelConfig:
    return TelegramChannelConfig(
        enabled=True,
        bot_token="test-token",
        mode="polling",
        polling=PollingConfig(
            timeout_seconds=1,
            limit=100,
            persist_offset=persist_offset,
            drop_pending_on_start=False,
        ),
        access=AccessConfig(dm_policy="allowlist", allow_from_user_ids=[]),
        pairing=PairingConfig(enabled=False, mode="off"),
        actions=ActionsConfig(
            send_message=True,
            edit_message=False,
            reactions=False,
            inline_buttons=False,
        ),
    )


def _build_runner(
    config: TelegramChannelConfig,
    state_store: TelegramPollStateStore,
    *,
    account_id: str,
) -> TelegramPollingRunner:
    api = _StubAPI()
    delivery = TelegramDeliveryService(
        api=api,  # type: ignore[arg-type]
        delivery_config=config.delivery,
        reply_config=config.reply,
        sleep_fn=lambda _s: None,
    )
    return TelegramPollingRunner(
        config=config,
        api=api,  # type: ignore[arg-type]
        runtime=_StubRuntime(),
        delivery=delivery,
        state_store=state_store,
        pairing_service=None,
        account_id=account_id,
        sleep_fn=lambda _s: None,
    )


def test_commit_offset_persists_through_fresh_store(tmp_path: Path) -> None:
    db_path = tmp_path / "poll.db"
    account_id = "telegram-bot:test"

    store_a = TelegramPollStateStore(db_path)
    runner = _build_runner(
        _make_config(persist_offset=True),
        store_a,
        account_id=account_id,
    )

    runner._commit_offset(105)
    assert store_a.get_last_update_id(account_id) == 105

    store_a.close()

    store_b = TelegramPollStateStore(db_path)
    try:
        assert store_b.get_last_update_id(account_id) == 105
    finally:
        store_b.close()


def test_commit_offset_does_not_persist_when_disabled(tmp_path: Path) -> None:
    db_path = tmp_path / "poll.db"
    account_id = "telegram-bot:test"

    store = TelegramPollStateStore(db_path)
    runner = _build_runner(
        _make_config(persist_offset=False),
        store,
        account_id=account_id,
    )

    # In-memory runner offset still moves forward (so the poll loop doesn't
    # re-request the same updates), but the persistent store is untouched.
    runner._commit_offset(42)
    assert runner._last_update_id == 42
    assert store.get_last_update_id(account_id) == 0

    store.close()

    store_b = TelegramPollStateStore(db_path)
    try:
        assert store_b.get_last_update_id(account_id) == 0
    finally:
        store_b.close()
