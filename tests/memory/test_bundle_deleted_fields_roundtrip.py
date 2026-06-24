from __future__ import annotations

from dataclasses import asdict

from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.portability.codec import (
    read_bundle_snapshot,
    write_bundle_snapshot,
)
from openminion.modules.memory.portability.models import (
    MemoryBundleImportOptions,
    MemoryBundleSnapshot,
)
from openminion.modules.memory.portability.merger import MemoryMerger
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from sophiagraph.portability.codec import _record_from_dict


def _base_record(
    *,
    is_deleted: bool = False,
    deleted_at: str | None = None,
    deleted_reason: str | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        id="m-roundtrip",
        scope="agent:test",
        type="fact",
        content={"text": "rt"},
        created_at="2026-05-18T00:00:00Z",
        updated_at="2026-05-18T00:00:00Z",
        source="tool_output",
        is_deleted=is_deleted,
        deleted_at=deleted_at,
        deleted_reason=deleted_reason,
    )


class TestDeletedFieldsRoundTrip:
    def test_legacy_record_round_trips(self):
        record = _base_record()
        payload = asdict(record)
        payload.pop("deleted_at", None)
        payload.pop("deleted_reason", None)
        revived = _record_from_dict(payload)
        assert revived.is_deleted is False
        assert revived.deleted_at is None
        assert revived.deleted_reason is None

    def test_audit_fields_survive_round_trip(self):
        record = _base_record(
            is_deleted=True,
            deleted_at="2026-05-18T01:23:45Z",
            deleted_reason="operator-requested forget",
        )
        revived = _record_from_dict(asdict(record))
        assert revived.is_deleted is True
        assert revived.deleted_at == "2026-05-18T01:23:45Z"
        assert revived.deleted_reason == "operator-requested forget"

    def test_is_deleted_without_audit_fields_round_trips(self):

        record = _base_record(is_deleted=True)
        revived = _record_from_dict(asdict(record))
        assert revived.is_deleted is True
        assert revived.deleted_at is None
        assert revived.deleted_reason is None


def _audit_record() -> MemoryRecord:
    return MemoryRecord(
        id="bundle-deleted-r1",
        scope="agent:bundle-src",
        type="fact",
        content={"text": "soft-deleted with audit metadata"},
        created_at="2026-05-18T00:00:00Z",
        updated_at="2026-05-18T01:23:45Z",
        source="tool_output",
        is_deleted=True,
        deleted_at="2026-05-18T01:23:45Z",
        deleted_reason="bundle-export integration smoke",
    )


def _live_record() -> MemoryRecord:
    return MemoryRecord(
        id="bundle-live-r2",
        scope="agent:bundle-src",
        type="fact",
        content={"text": "live, never deleted"},
        created_at="2026-05-18T00:00:00Z",
        updated_at="2026-05-18T00:00:00Z",
        source="tool_output",
    )


def _snapshot() -> MemoryBundleSnapshot:
    return MemoryBundleSnapshot(
        manifest={
            "bundle_id": "bundle-mpf07-integration",
            "created_at": "2026-05-18T00:00:00Z",
            "source_backend": "SQLiteMemoryStore",
            "source_instance": {"store_class": "SQLiteMemoryStore"},
            "scopes": ["agent:bundle-src"],
            "filters": {"types": [], "limit": None},
        },
        records=[_audit_record(), _live_record()],
    )


def test_audit_fields_survive_disk_round_trip(tmp_path) -> None:
    bundle_path = tmp_path / "bundle.tar.gz"
    written = write_bundle_snapshot(_snapshot(), bundle_path)
    reloaded = read_bundle_snapshot(written)

    audit_revived = next(r for r in reloaded.records if r.id == "bundle-deleted-r1")
    assert audit_revived.is_deleted
    assert audit_revived.deleted_at == "2026-05-18T01:23:45Z"
    assert audit_revived.deleted_reason == "bundle-export integration smoke"

    live_revived = next(r for r in reloaded.records if r.id == "bundle-live-r2")
    assert not live_revived.is_deleted
    assert live_revived.deleted_at is None
    assert live_revived.deleted_reason is None


def test_audit_fields_persist_through_merger_import(tmp_path) -> None:
    bundle_path = tmp_path / "bundle.tar.gz"
    write_bundle_snapshot(_snapshot(), bundle_path)
    reloaded = read_bundle_snapshot(bundle_path)

    db_path = tmp_path / "target.db"
    target_store = SQLiteMemoryStore(db_path)
    merger = MemoryMerger(MemoryService(target_store))
    result = merger.import_snapshot(
        reloaded,
        MemoryBundleImportOptions(
            trust_mode="direct",
            conflict_mode="skip",
            id_mode="preserve",
        ),
    )
    assert result.applied
    assert result.imported_records == 2

    fresh_store = SQLiteMemoryStore(db_path)
    audit_row = fresh_store.get("bundle-deleted-r1")
    assert audit_row is not None
    assert audit_row.is_deleted
    assert audit_row.deleted_at == "2026-05-18T01:23:45Z"
    assert audit_row.deleted_reason == "bundle-export integration smoke"

    live_row = fresh_store.get("bundle-live-r2")
    assert live_row is not None
    assert not live_row.is_deleted
    assert live_row.deleted_at is None
    assert live_row.deleted_reason is None
