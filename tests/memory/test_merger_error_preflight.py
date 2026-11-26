from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.portability.merger import MemoryMerger
from openminion.modules.memory.portability.models import (
    MemoryBundleImportOptions,
    MemoryBundleSnapshot,
)
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite import SQLiteMemoryStore


def _make_record(
    record_id: str,
    *,
    content: str,
    scope: str = "agent:a",
    key: str | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        scope=scope,  # type: ignore[arg-type]
        type="fact",
        content=content,
        key=key,
        source="imported",
        confidence=0.9,
        created_at="2026-11-26T00:00:00Z",
        updated_at="2026-11-26T00:00:00Z",
    )


def test_error_mode_late_exact_id_conflict_writes_nothing_across_reopen(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(db_path)
    try:
        service = MemoryService(store)
        existing = _make_record("mem_conflict", content="original value", key="k_conflict")
        store.put(existing)

        snapshot = MemoryBundleSnapshot(
            manifest={"bundle_id": "b_err_1"},
            records=[
                _make_record("mem_new_first", content="should not persist", key="k_new"),
                replace(existing, content="conflicting updated value"),
            ],
        )
        result = MemoryMerger(service).import_snapshot(
            snapshot,
            MemoryBundleImportOptions(trust_mode="direct", conflict_mode="error"),
        )

        assert result.applied is False
        assert result.imported_records == 0
        assert result.skipped_records == 0
        assert len(result.conflicts) == 1
        assert result.conflicts[0]["record_id"] == "mem_conflict"
        assert result.conflicts[0]["reason"] == "exact_id_conflict"
        assert store.get("mem_new_first") is None
        assert store.get("mem_conflict") is not None
        assert store.get("mem_conflict").content == "original value"
    finally:
        store.close()

    # Persisted/reopened negative control
    reopened = SQLiteMemoryStore(db_path)
    try:
        assert reopened.get("mem_new_first") is None
        row = reopened.get("mem_conflict")
        assert row is not None
        assert row.content == "original value"
    finally:
        reopened.close()


def test_error_mode_duplicate_in_snapshot_exact_id_and_normalized_key_write_nothing(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    try:
        service = MemoryService(store)

        # Duplicate exact ID inside snapshot with divergent content
        dup_id_snapshot = MemoryBundleSnapshot(
            manifest={"bundle_id": "b_dup_id"},
            records=[
                _make_record("mem_clean", content="clean row"),
                _make_record("mem_dup", content="version A"),
                _make_record("mem_dup", content="version B"),
            ],
        )
        res_id = MemoryMerger(service).import_snapshot(
            dup_id_snapshot,
            MemoryBundleImportOptions(trust_mode="direct", conflict_mode="error"),
        )
        assert res_id.applied is False
        assert res_id.imported_records == 0
        assert res_id.conflicts[0]["reason"] == "exact_id_conflict"
        assert store.get("mem_clean") is None
        assert store.get("mem_dup") is None

        # Duplicate normalized key inside snapshot with different IDs
        dup_key_snapshot = MemoryBundleSnapshot(
            manifest={"bundle_id": "b_dup_key"},
            records=[
                _make_record("mem_k1", content="alpha", key="shared_key"),
                _make_record("mem_k2", content="beta", key="shared_key"),
            ],
        )
        res_key = MemoryMerger(service).import_snapshot(
            dup_key_snapshot,
            MemoryBundleImportOptions(trust_mode="direct", conflict_mode="error"),
        )
        assert res_key.applied is False
        assert res_key.imported_records == 0
        assert res_key.conflicts[0]["reason"] == "normalized_key_conflict"
        assert store.get("mem_k1") is None
        assert store.get("mem_k2") is None
    finally:
        store.close()


def test_healthy_dry_run_skip_supersede_and_id_remapping_neighbors(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    try:
        service = MemoryService(store)
        merger = MemoryMerger(service)

        # Healthy import in error mode
        healthy_snap = MemoryBundleSnapshot(
            manifest={"bundle_id": "b_healthy"},
            records=[_make_record("mem_1", content="hello", key="k1")],
        )
        dry_res = merger.import_snapshot(
            healthy_snap,
            MemoryBundleImportOptions(
                trust_mode="direct", conflict_mode="error", dry_run=True
            ),
        )
        assert dry_res.applied is False
        assert dry_res.imported_records == 1
        assert store.get("mem_1") is None

        ok_res = merger.import_snapshot(
            healthy_snap,
            MemoryBundleImportOptions(trust_mode="direct", conflict_mode="error"),
        )
        assert ok_res.applied is True
        assert ok_res.imported_records == 1
        assert store.get("mem_1") is not None

        # Skip mode leaves conflicting existing row unchanged and imports new neighbor
        conflict_snap = MemoryBundleSnapshot(
            manifest={"bundle_id": "b_skip"},
            records=[
                _make_record("mem_2", content="new row", key="k2"),
                _make_record("mem_1", content="divergent", key="k1"),
            ],
        )
        skip_res = merger.import_snapshot(
            conflict_snap,
            MemoryBundleImportOptions(trust_mode="direct", conflict_mode="skip"),
        )
        assert skip_res.applied is True
        assert skip_res.imported_records == 1
        assert skip_res.skipped_records == 1
        assert store.get("mem_1").content == "hello"
        assert store.get("mem_2") is not None

        # Supersede mode on exact-ID conflict remaps ID and preserves original row
        existing_no_key = _make_record("mem_sup_target", content="v1", key=None)
        store.put(existing_no_key)
        sup_snap = MemoryBundleSnapshot(
            manifest={"bundle_id": "b_sup"},
            records=[_make_record("mem_sup_target", content="v2", key=None)],
        )
        sup_res = merger.import_snapshot(
            sup_snap,
            MemoryBundleImportOptions(
                trust_mode="direct",
                conflict_mode="supersede",
                id_mode="preserve",
            ),
        )
        assert sup_res.applied is True
        assert sup_res.imported_records == 1
        assert store.get("mem_sup_target").content == "v1"

        # ID remapping (id_mode="regenerate") mints a fresh ID in error mode
        regen_snap = MemoryBundleSnapshot(
            manifest={"bundle_id": "b_regen"},
            records=[_make_record("mem_orig_regen", content="regenerated row", key="k_regen")],
        )
        regen_res = merger.import_snapshot(
            regen_snap,
            MemoryBundleImportOptions(
                trust_mode="direct",
                conflict_mode="error",
                id_mode="regenerate",
            ),
        )
        assert regen_res.applied is True
        assert regen_res.imported_records == 1
        assert store.get("mem_orig_regen") is None
        live = service.find_record_by_normalized_key(
            scope="agent:a", record_type="fact", normalized_key="k_regen"
        )
        assert live is not None
        assert live.content == "regenerated row"
        assert live.id != "mem_orig_regen"
    finally:
        store.close()
