from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from openminion.modules.controlplane.constants import DEFAULT_MINIMAL_SCOPES
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore
from openminion.modules.controlplane.channels.telegram.config import PairingConfig
from openminion.modules.controlplane.channels.telegram.models import (
    TelegramInboundEnvelope,
    TelegramUser,
)
from openminion.modules.controlplane.channels.telegram.pairing import (
    TelegramPairingService,
)
from openminion.modules.controlplane.channels.telegram.state import (
    TelegramPollStateStore,
)


def _env(
    *, text: str, user_id: int = 11, chat_id: int = 22, chat_type: str = "private"
) -> TelegramInboundEnvelope:
    return TelegramInboundEnvelope(
        update_id=1,
        raw_type="message",
        chat_id=chat_id,
        message_id=10,
        text=text,
        from_user=TelegramUser(id=user_id, username="u", display="U"),
        chat_type=chat_type,
        topic_id=None,
        raw_update={},
    )


def _service(
    tmp_path: Path,
    *,
    controlplane_store: SQLiteControlPlaneStore | None = None,
    **config_kwargs: object,
) -> tuple[TelegramPollStateStore, TelegramPairingService]:
    store = TelegramPollStateStore(str(tmp_path / "pairing.db"))
    service = TelegramPairingService(
        config=PairingConfig(**config_kwargs),
        store=store,
        controlplane_store=controlplane_store,
    )
    return store, service


def test_issue_token_hashes_value_and_does_not_store_plaintext(tmp_path: Path) -> None:
    db = tmp_path / "pairing.db"
    store, service = _service(tmp_path, hash_pepper="pepper")

    issued = service.issue_token(
        expected_user_id=123, expected_chat_id=None, token="abcdEFGH_123"
    )

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT token_hash, token_hint, scopes_json FROM telegram_pair_tokens"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] != issued.token
    assert row[1] == issued.token_hint
    assert "abcdEFGH_123" not in str(row)


def test_consume_token_single_use_and_user_binding(tmp_path: Path) -> None:
    store, service = _service(tmp_path, hash_pepper="pepper")

    issued = service.issue_token(
        expected_user_id=11, expected_chat_id=22, token="token_123"
    )

    ok = service.handle_start_pairing(
        _env(text=f"/start {issued.token}"), bot_username="mybot"
    )
    assert ok.handled is True
    assert ok.reply_text == "Paired ✅"

    reused = service.handle_start_pairing(
        _env(text=f"/start {issued.token}"), bot_username="mybot"
    )
    assert reused.handled is True
    assert reused.reply_text == "Pairing failed or expired. Generate a new link."


def test_consume_token_enforces_expected_user(tmp_path: Path) -> None:
    store, service = _service(tmp_path, hash_pepper="pepper")

    issued = service.issue_token(
        expected_user_id=99, expected_chat_id=None, token="token_456"
    )

    denied = service.handle_start_pairing(
        _env(text=f"/start {issued.token}", user_id=11), bot_username="mybot"
    )
    assert denied.handled is True
    assert denied.reply_text == "Pairing failed or expired. Generate a new link."

    accepted = service.handle_start_pairing(
        _env(text=f"/start {issued.token}", user_id=99), bot_username="mybot"
    )
    assert accepted.reply_text == "Paired ✅"


def test_pairing_rate_limit_per_user(tmp_path: Path) -> None:
    store, service = _service(
        tmp_path,
        hash_pepper="pepper",
        attempt_window_seconds=60,
        max_attempts_per_user=1,
        max_attempts_per_chat=10,
    )

    first = service.handle_start_pairing(
        _env(text="/start invalid_token"), bot_username="mybot"
    )
    second = service.handle_start_pairing(
        _env(text="/start invalid_token2"), bot_username="mybot"
    )

    assert first.reply_text == "Pairing failed or expired. Generate a new link."
    assert second.reply_text == "Too many pairing attempts. Try again shortly."


def test_issue_token_rejects_invalid_charset_or_length(tmp_path: Path) -> None:
    _store, service = _service(tmp_path)
    with pytest.raises(ValueError):
        service.issue_token(
            expected_user_id=1, expected_chat_id=1, token="bad token with spaces"
        )


def test_pairing_mode_off_does_not_handle_start(tmp_path: Path) -> None:
    store, service = _service(tmp_path, mode="off", enabled=True)

    result = service.handle_start_pairing(
        _env(text="/start token_1"), bot_username="mybot"
    )
    assert result.handled is False


def test_successful_pairing_bridges_to_controlplane_pairings(tmp_path: Path) -> None:
    controlplane_store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    telegram_store, service = _service(
        tmp_path,
        hash_pepper="pepper",
        controlplane_store=controlplane_store,
    )

    issued = service.issue_token(
        expected_user_id=11, expected_chat_id=22, token="bridge_token_1"
    )
    result = service.handle_start_pairing(
        _env(text=f"/start {issued.token}"), bot_username="mybot"
    )
    assert result.handled is True
    assert result.reply_text == "Paired ✅"

    pairing = controlplane_store.get_pairing(channel="telegram", chat_id="22")
    assert pairing is not None
    assert pairing["channel"] == "telegram"
    assert pairing["chat_id"] == "22"
    assert pairing["user_id"] == "11"
    assert str(pairing.get("session_id") or "").strip() != ""
    assert list(pairing.get("scopes") or []) == list(DEFAULT_MINIMAL_SCOPES)
    principal_id = controlplane_store.resolve_principal(
        channel="telegram", subject_id="22"
    )
    assert principal_id == pairing["pairing_id"]
    binding = controlplane_store.get_channel_subject(
        channel="telegram", subject_id="22"
    )
    assert binding is not None
    assert binding["principal_id"] == principal_id
    assert binding["meta"]["source"] == "cp_pairings_dual_write"

    telegram_store.close()
    controlplane_store.close()
