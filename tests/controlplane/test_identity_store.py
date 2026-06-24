from __future__ import annotations

from pathlib import Path

from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore


def test_sqlite_store_has_principal_identity_tables(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    try:
        tables = {
            row["name"]
            for row in store._conn.execute(  # noqa: SLF001
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "cp_principals" in tables
        assert "cp_channel_subjects" in tables
    finally:
        store.close()


def test_sqlite_store_principal_bind_and_resolve_per_room(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    try:
        principal_id = store.upsert_principal(meta={"kind": "telegram_room"})
        store.bind_principal_subject(
            principal_id=principal_id,
            channel="telegram",
            subject_id="100",
            scopes=["cp.message.read", "cp.message.write"],
            status="active",
            note="p3b-v1",
            meta={"topic_id": "200"},
        )

        resolved = store.resolve_principal(channel="telegram", subject_id="100")
        binding = store.get_channel_subject(channel="telegram", subject_id="100")

        assert resolved == principal_id
        assert binding is not None
        assert binding["principal_id"] == principal_id
        assert binding["channel"] == "telegram"
        assert binding["subject_id"] == "100"
        assert binding["status"] == "active"
        assert "cp.message.read" in list(binding.get("scopes") or [])
        assert binding["meta"]["topic_id"] == "200"
    finally:
        store.close()


def test_sqlite_store_principal_resolve_skips_non_active_binding(
    tmp_path: Path,
) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    try:
        principal_id = store.upsert_principal()
        store.bind_principal_subject(
            principal_id=principal_id,
            channel="telegram",
            subject_id="200",
            status="paused",
            scopes=["cp.message.read"],
        )
        assert store.resolve_principal(channel="telegram", subject_id="200") is None
    finally:
        store.close()


def test_inmemory_store_principal_bind_and_resolve() -> None:
    store = InMemoryControlPlaneStore()
    principal_id = store.upsert_principal(principal_id="principal-room-1")
    store.bind_principal_subject(
        principal_id=principal_id,
        channel="telegram",
        subject_id="300",
        status="active",
        scopes=["cp.message.read"],
        meta={"topic_id": "99"},
    )
    assert store.resolve_principal(channel="telegram", subject_id="300") == principal_id
    binding = store.get_channel_subject(channel="telegram", subject_id="300")
    assert binding is not None
    assert binding["meta"]["topic_id"] == "99"


def test_sqlite_upsert_pairing_dual_writes_principal_mapping(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    try:
        pairing_id = store.upsert_pairing(
            channel="telegram",
            chat_id="400",
            user_id="42",
            session_id="sess-400",
            scopes=["cp.message.read"],
            status="active",
            note="dual-write",
        )
        assert (
            store.resolve_principal(channel="telegram", subject_id="400") == pairing_id
        )
        binding = store.get_channel_subject(channel="telegram", subject_id="400")
        assert binding is not None
        assert binding["principal_id"] == pairing_id
        assert binding["meta"]["source"] == "cp_pairings_dual_write"
        assert binding["meta"]["user_id"] == "42"
        assert binding["meta"]["session_id"] == "sess-400"
    finally:
        store.close()


def test_sqlite_backfill_pairings_to_principals_roundtrip(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    try:
        pairing_id = store.upsert_pairing(
            channel="telegram",
            chat_id="500",
            user_id="55",
            session_id="sess-500",
            scopes=["cp.message.read", "cp.message.write"],
            status="active",
            note="legacy-row",
        )
        with store._lock, store._conn:  # noqa: SLF001
            store._conn.execute(  # noqa: SLF001
                "DELETE FROM cp_channel_subjects WHERE channel = ? AND subject_id = ?",
                ("telegram", "500"),
            )
            store._conn.execute(  # noqa: SLF001
                "DELETE FROM cp_principals WHERE principal_id = ?",
                (pairing_id,),
            )

        report = store.backfill_pairings_to_principals(channel="telegram")
        assert report["scanned"] >= 1
        assert report["principal_new"] >= 1
        assert report["subject_new"] >= 1

        assert (
            store.resolve_principal(channel="telegram", subject_id="500") == pairing_id
        )
        binding = store.get_channel_subject(channel="telegram", subject_id="500")
        assert binding is not None
        assert binding["meta"]["source"] == "cp_pairings_backfill"
        assert binding["meta"]["user_id"] == "55"
    finally:
        store.close()
