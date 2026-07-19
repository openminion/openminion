from __future__ import annotations

from pathlib import Path

from openminion.modules.controlplane.channels.telegram.state import (
    TelegramPollStateStore,
)
from openminion.modules.controlplane.pairing import ControlPlanePairingStore
from openminion.modules.controlplane.pairing.migration import PairingMigrationJob
from openminion.modules.controlplane.runtime.audit import AuditLogger
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore


def test_pairing_migration_is_noop_for_fresh_legacy_store(tmp_path: Path) -> None:
    legacy = TelegramPollStateStore(str(tmp_path / "telegram.db"))
    cp_store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    audit = AuditLogger()
    try:
        result = PairingMigrationJob(
            legacy_store=legacy,
            new_store=ControlPlanePairingStore(cp_store),
            audit_logger=audit,
        ).run_once()
        assert result == {"tokens_copied": 0, "attempts_copied": 0, "skipped": 0}
        assert audit.events == []
    finally:
        legacy.close()
        cp_store.close()


def test_pairing_migration_copies_tokens_and_attempts_once(tmp_path: Path) -> None:
    legacy = TelegramPollStateStore(str(tmp_path / "telegram.db"))
    cp_store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    audit = AuditLogger()
    try:
        legacy.issue_pair_token(
            token="token_1",
            token_ttl_seconds=60,
            scopes=["chat.interact"],
            expected_user_id=11,
            expected_chat_id=22,
            hash_pepper="pepper",
        )
        legacy.record_pair_attempt(
            token="bad",
            user_id=11,
            chat_id=22,
            outcome="invalid_token",
            hash_pepper="pepper",
        )
        job = PairingMigrationJob(
            legacy_store=legacy,
            new_store=ControlPlanePairingStore(cp_store),
            audit_logger=audit,
        )
        assert job.run_once() == {
            "tokens_copied": 1,
            "attempts_copied": 1,
            "skipped": 0,
        }
        assert job.run_once() == {
            "tokens_copied": 0,
            "attempts_copied": 0,
            "skipped": 1,
        }
        assert cp_store.has_pair_channel_data(channel="telegram") is True
        assert audit.events[0].event_type == "cp.pairing.migration.completed"
        assert len(legacy.iter_pair_tokens()) == 1
    finally:
        legacy.close()
        cp_store.close()
