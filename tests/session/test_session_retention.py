from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import partial
import hashlib
from types import SimpleNamespace

import pytest

from openminion.modules.session.retention import (
    SessionRetentionBlockedError,
    SessionRetentionPolicy,
    SessionRetentionService,
    SessionRetentionSnapshotChangedError,
)
from openminion.modules.session.sharing import SessionShareService
from openminion.modules.session.storage import SQLiteSessionStore
from openminion.base.config.env import EnvironmentConfig
from openminion.tools.blockchain.preparations import (
    purge_blockchain_session_records,
    save_prepared_transaction,
)
from openminion.tools.blockchain.runtime import preparation_digest


def _old() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()


def _blockchain_env(tmp_path) -> EnvironmentConfig:
    return EnvironmentConfig(
        values={
            "OPENMINION_HOME": str(tmp_path),
            "OPENMINION_DATA_ROOT": str(tmp_path / ".openminion"),
        }
    )


def _save_blockchain_record(tmp_path, session_id: str) -> None:
    transaction = {
        "schema_version": "evm-transaction-v1",
        "transaction_type": "eip1559",
        "chain_id": 31337,
        "from_address": "0x" + "11" * 20,
        "to_address": "0x" + "22" * 20,
        "value_wei": "1",
        "nonce": "0",
        "gas_limit": "21000",
        "data": "0x",
        "max_fee_per_gas_wei": "2",
        "max_priority_fee_per_gas_wei": "1",
        "max_total_fee_wei": "42000",
    }
    save_prepared_transaction(
        {
            "transaction": transaction,
            "call_context": None,
            "preparation_digest": preparation_digest(transaction, None),
        },
        SimpleNamespace(session_id=session_id, env=_blockchain_env(tmp_path)),
    )


def test_retention_dry_run_and_purge_delete_dependent_rows() -> None:
    store = SQLiteSessionStore(":memory:")
    sid = store.create_session(session_id="retention-old")
    store.append_turn(sid, "user", "delete me")
    store.update_summary(sid, "summary", based_on_seq=1)
    store._record_store.update_rows(
        "sessions", {"session_id": sid}, {"updated_at": _old(), "status": "closed"}
    )
    service = SessionRetentionService(store)
    plan = service.dry_run(
        policy=SessionRetentionPolicy(
            inactivity_ttl_seconds=1, closed_retention_seconds=1
        )
    )

    assert [item.session_id for item in plan.candidates] == [sid]
    result = service.purge(plan)

    assert result["purged_session_count"] == 1
    assert store.get_session(sid) is None
    assert store.list_turns(sid) == []


def test_retention_blocks_active_hold_lease_and_share(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    sid = store.create_session(session_id="retention-blocked")
    store._record_store.update_rows(
        "sessions", {"session_id": sid}, {"updated_at": _old(), "status": "closed"}
    )
    SessionShareService(store).create_share(
        session_id=sid, created_by="alice", ttl_seconds=600
    )
    store.acquire_session_turn_lease(sid, owner="worker", request_id="r1", ttl_s=600)
    _save_blockchain_record(tmp_path, sid)
    service = SessionRetentionService(
        store,
        purge_session_records=partial(
            purge_blockchain_session_records,
            env=_blockchain_env(tmp_path),
        ),
    )
    service.add_hold(session_id=sid, reason="legal")
    store._record_store.update_rows(
        "sessions", {"session_id": sid}, {"updated_at": _old(), "status": "closed"}
    )
    plan = service.dry_run(
        policy=SessionRetentionPolicy(
            inactivity_ttl_seconds=1, closed_retention_seconds=1
        )
    )

    assert set(plan.candidates[0].blockers) == {
        "retention_hold",
        "active_turn_lease",
        "active_share",
    }
    with pytest.raises(SessionRetentionBlockedError):
        service.purge(plan)
    session_key = hashlib.sha256(sid.encode()).hexdigest()
    assert (tmp_path / ".openminion" / "blockchain" / "sessions" / session_key).is_dir()


def test_retention_changed_snapshot_refuses_purge() -> None:
    store = SQLiteSessionStore(":memory:")
    sid = store.create_session(session_id="retention-drift")
    store._record_store.update_rows(
        "sessions", {"session_id": sid}, {"updated_at": _old(), "status": "closed"}
    )
    service = SessionRetentionService(store)
    plan = service.dry_run(
        policy=SessionRetentionPolicy(
            inactivity_ttl_seconds=1, closed_retention_seconds=1
        )
    )
    store.create_session(session_id="newer")
    store._record_store.update_rows(
        "sessions", {"session_id": "newer"}, {"updated_at": _old(), "status": "closed"}
    )

    with pytest.raises(SessionRetentionSnapshotChangedError):
        service.purge(plan)


def test_retention_purges_blockchain_records_before_database_rows(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    sid = store.create_session(session_id="retention-blockchain")
    store._record_store.update_rows(
        "sessions", {"session_id": sid}, {"updated_at": _old(), "status": "closed"}
    )
    _save_blockchain_record(tmp_path, sid)
    service = SessionRetentionService(
        store,
        purge_session_records=partial(
            purge_blockchain_session_records,
            env=_blockchain_env(tmp_path),
        ),
    )
    plan = service.dry_run(
        policy=SessionRetentionPolicy(
            inactivity_ttl_seconds=1, closed_retention_seconds=1
        )
    )

    result = service.purge(plan)

    session_key = hashlib.sha256(sid.encode()).hexdigest()
    record_root = tmp_path / ".openminion" / "blockchain" / "sessions" / session_key
    assert result["purged_session_count"] == 1
    assert not record_root.exists()
    assert store.get_session(sid) is None


def test_retention_domain_failure_preserves_database_and_retry_converges(
    tmp_path, monkeypatch
) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    sid = store.create_session(session_id="retention-retry")
    store._record_store.update_rows(
        "sessions", {"session_id": sid}, {"updated_at": _old(), "status": "closed"}
    )
    _save_blockchain_record(tmp_path, sid)
    purge = partial(
        purge_blockchain_session_records,
        env=_blockchain_env(tmp_path),
    )
    service = SessionRetentionService(store, purge_session_records=purge)
    plan = service.dry_run(
        policy=SessionRetentionPolicy(
            inactivity_ttl_seconds=1, closed_retention_seconds=1
        )
    )

    def fail_filesystem(_session_id: str) -> None:
        raise OSError("filesystem unavailable")

    monkeypatch.setattr(service, "_purge_session_records", fail_filesystem)
    with pytest.raises(OSError, match="filesystem unavailable"):
        service.purge(plan)
    assert store.get_session(sid) is not None

    monkeypatch.setattr(service, "_purge_session_records", purge)
    original = service._purge_session

    def fail_database(_session_id: str) -> int:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(service, "_purge_session", fail_database)
    with pytest.raises(RuntimeError, match="database unavailable"):
        service.purge(plan)
    assert store.get_session(sid) is not None

    monkeypatch.setattr(service, "_purge_session", original)
    service.purge(plan)
    assert store.get_session(sid) is None
