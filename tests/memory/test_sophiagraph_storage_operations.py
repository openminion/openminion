from __future__ import annotations

from pathlib import Path

from sophiagraph import (
    MemoryNamespace,
    MemoryRecord,
    SophiaGraphSqliteStore,
    acquire_write_lease,
    create_backup,
    create_retention_snapshot,
    release_write_lease,
    restore_backup,
    verify_retention_snapshot,
)


def _record(record_id: str) -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        scope="agent:ops",
        type="fact",
        key=record_id,
        title=record_id,
        content={"text": record_id},
        created_at="2026-06-10T00:00:00+00:00",
        updated_at="2026-06-10T00:00:00+00:00",
        namespace=MemoryNamespace(tenant_id="tenant", agent_id="ops", graph_id="main"),
    )


def test_sophiagraph_storage_operations_public_import_cycle(tmp_path: Path) -> None:
    source = SophiaGraphSqliteStore(tmp_path / "source.sqlite3")
    source.put_record(_record("rec-1"))
    source.put_record(_record("rec-2"))

    lease = acquire_write_lease(
        source,
        owner="openminion-fixture",
        ttl_seconds=5,
        heartbeat_seconds=10,
    )
    backup_dir = tmp_path / "backup"
    try:
        descriptor = create_backup(source, backup_dir)
    finally:
        release_write_lease(lease, store=source)

    restored = SophiaGraphSqliteStore(tmp_path / "restored.sqlite3")
    outcome = restore_backup(restored, backup_dir)
    assert outcome.restored is True
    assert restored.get_record("rec-1") is not None
    assert restored.get_record("rec-2") is not None

    snapshot = create_retention_snapshot(
        restored,
        name="compliance-cut",
        namespace=MemoryNamespace(tenant_id="tenant", agent_id="ops", graph_id="main"),
    )
    report = verify_retention_snapshot(
        restored,
        name="compliance-cut",
        namespace=snapshot.namespace,
    )

    assert descriptor.kind == "physical_sqlite"
    assert report.verified is True
