from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from openminion.modules.a2a.models import AuditRecord
from openminion.modules.a2a.storage import (
    ARCHIVE_TABLE_NAME,
    PostgresAuditArchiveStore,
    SQLiteAuditStore,
)

pytestmark = pytest.mark.postgres


def _open_postgres_admin_and_scoped():
    postgres_url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")

    try:
        from sqlalchemy import create_engine, text
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"sqlalchemy unavailable: {exc}")

    try:
        from openminion.modules.storage.backends.postgres import (
            RecordStorePostgres,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"postgres backend unavailable: {exc}")

    schema_name = f"smp18_{uuid.uuid4().hex}"
    admin_engine = create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))

    scoped_engine = create_engine(
        postgres_url,
        future=True,
        connect_args={"options": f"-csearch_path={schema_name}"},
    )
    record_store = RecordStorePostgres(scoped_engine)

    def _cleanup() -> None:
        try:
            record_store.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            scoped_engine.dispose()
        except Exception:  # noqa: BLE001
            pass
        try:
            with admin_engine.begin() as conn2:
                conn2.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        except Exception:  # noqa: BLE001
            pass
        try:
            admin_engine.dispose()
        except Exception:  # noqa: BLE001
            pass

    return scoped_engine, record_store, _cleanup


def _audit_root(tmp_path: Path) -> Path:
    audit_root = tmp_path / "audit"
    audit_root.mkdir()
    return audit_root


class _StaticStore:
    def __init__(self, *, insert_result: int = 0) -> None:
        self.insert_result = insert_result

    def insert_many(self, table, rows):  # noqa: ANN001, ARG002
        return self.insert_result

    def query_dicts(self, sql, params):  # noqa: ANN001, ARG002
        return []


class _RecordingStore:
    def __init__(self) -> None:
        self.inserted: list[tuple[str, list[dict]]] = []

    def insert_many(self, table, rows):  # noqa: ANN001
        self.inserted.append((table, list(rows)))
        return len(rows)

    def query_dicts(self, sql, params):  # noqa: ANN001, ARG002
        return []


class _ExplodingStore:
    def query_dicts(self, sql, params):  # noqa: ANN001, ARG002
        raise AssertionError("archive should not be queried when flag is off")

    def insert_many(self, table, rows):  # noqa: ANN001, ARG002
        raise AssertionError("archive should not be written when flag is off")


def _audit_record(
    *,
    ts: str,
    msg_id: str,
    trace_id: str,
    method: str = "job.start",
    status: str = "SUCCESS",
    error_code: str | None = None,
) -> AuditRecord:
    return AuditRecord(
        ts=ts,
        msg_id=msg_id,
        trace_id=trace_id,
        from_agent="agent.from",
        to_agent="agent.to",
        to_capability=None,
        type="call",
        method=method,
        status=status,
        task_id=None,
        error_code=error_code,
        error_message=None if error_code is None else "boom",
        envelope={"msg_id": msg_id},
        data={"ok": error_code is None},
    )


def test_postgres_audit_archive_store_has_canonical_table_name() -> None:
    assert ARCHIVE_TABLE_NAME == "a2a_audit_archive"
    assert PostgresAuditArchiveStore.table_name == ARCHIVE_TABLE_NAME


def test_archive_files_older_than_rejects_zero_or_negative(tmp_path: Path) -> None:
    archive = PostgresAuditArchiveStore(
        record_store=_StaticStore(),  # type: ignore[arg-type]
        audit_root=_audit_root(tmp_path),
    )
    with pytest.raises(ValueError):
        archive.archive_files_older_than(0)
    with pytest.raises(ValueError):
        archive.archive_files_older_than(-3)


def test_archive_skips_files_inside_retention_window(tmp_path: Path) -> None:
    audit_root = _audit_root(tmp_path)
    today = datetime.now(timezone.utc).date().isoformat()
    (audit_root / f"{today}.db").write_bytes(b"")
    store = _RecordingStore()
    archive = PostgresAuditArchiveStore(
        record_store=store,  # type: ignore[arg-type]
        audit_root=audit_root,
    )
    report = archive.archive_files_older_than(7)
    assert report.archived_files == []
    assert report.archived_rows == 0
    assert f"{today}.db" in report.skipped_files
    assert store.inserted == []


def test_archive_handles_unparseable_filename_safely(tmp_path: Path) -> None:
    audit_root = _audit_root(tmp_path)
    (audit_root / "junk.db").write_bytes(b"")
    archive = PostgresAuditArchiveStore(
        record_store=_StaticStore(),  # type: ignore[arg-type]
        audit_root=audit_root,
    )
    report = archive.archive_files_older_than(7)
    assert "junk.db" in report.skipped_files
    assert (audit_root / "junk.db").exists()


def test_archive_to_dict_roundtrip(tmp_path: Path) -> None:
    archive = PostgresAuditArchiveStore(
        record_store=_StaticStore(),  # type: ignore[arg-type]
        audit_root=_audit_root(tmp_path),
    )
    report = archive.archive_files_older_than(30)
    payload = report.to_dict()
    assert set(payload.keys()) == {
        "archived_files",
        "archived_rows",
        "skipped_files",
        "deleted_files",
    }
    assert payload["archived_rows"] == 0


def test_archive_file_inserts_rows_into_postgres(tmp_path: Path) -> None:
    engine, record_store, cleanup = _open_postgres_admin_and_scoped()
    try:
        audit_root = tmp_path / "audit"
        sqlite_store = SQLiteAuditStore(audit_root, retention_days=14)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        sqlite_store.append_audit(
            _audit_record(ts=old_ts, msg_id="msg-1", trace_id="trace-1")
        )
        sqlite_store.append_audit(
            _audit_record(
                ts=old_ts,
                msg_id="msg-2",
                trace_id="trace-2",
                status="FAILED",
                error_code="OOPS",
            )
        )
        sqlite_store.close()
        day_file = audit_root / f"{old_ts[:10]}.db"
        assert day_file.exists()

        archive = PostgresAuditArchiveStore(
            record_store=record_store,
            audit_root=audit_root,
            engine=engine,
        )

        inserted = archive.archive_file(day_file)
        assert inserted == 2

        # Read back via the archive's own typed query method.
        results = archive.query_archive({"limit": 10})
        assert {r.msg_id for r in results} == {"msg-1", "msg-2"}
        # error_only filter narrows.
        errs = archive.query_archive({"error_only": True})
        assert [r.msg_id for r in errs] == ["msg-2"]
    finally:
        cleanup()


def test_archive_files_older_than_deletes_by_default(tmp_path: Path) -> None:

    engine, record_store, cleanup = _open_postgres_admin_and_scoped()
    try:
        audit_root = tmp_path / "audit"
        sqlite_store = SQLiteAuditStore(audit_root, retention_days=365)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        fresh_ts = datetime.now(timezone.utc).isoformat()
        sqlite_store.append_audit(
            _audit_record(ts=old_ts, msg_id="old-1", trace_id="trace-old")
        )
        sqlite_store.append_audit(
            _audit_record(ts=fresh_ts, msg_id="new-1", trace_id="trace-new")
        )
        sqlite_store.close()

        archive = PostgresAuditArchiveStore(
            record_store=record_store,
            audit_root=audit_root,
            engine=engine,
        )

        report = archive.archive_files_older_than(7)
        assert report.archived_rows == 1
        assert len(report.archived_files) == 1
        assert len(report.deleted_files) == 1
        assert not (audit_root / f"{old_ts[:10]}.db").exists()
        assert (audit_root / f"{fresh_ts[:10]}.db").exists()
    finally:
        cleanup()


def test_archive_files_older_than_keep_files(tmp_path: Path) -> None:
    engine, record_store, cleanup = _open_postgres_admin_and_scoped()
    try:
        audit_root = tmp_path / "audit"
        sqlite_store = SQLiteAuditStore(audit_root, retention_days=365)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        sqlite_store.append_audit(
            _audit_record(ts=old_ts, msg_id="keep-1", trace_id="trace-keep")
        )
        sqlite_store.close()

        archive = PostgresAuditArchiveStore(
            record_store=record_store,
            audit_root=audit_root,
            engine=engine,
        )

        report = archive.archive_files_older_than(7, keep_files=True)
        assert report.archived_rows == 1
        assert report.deleted_files == []
        assert (audit_root / f"{old_ts[:10]}.db").exists()
    finally:
        cleanup()


def test_query_audit_include_archive_unions_sqlite_and_postgres(
    tmp_path: Path,
) -> None:
    engine, record_store, cleanup = _open_postgres_admin_and_scoped()
    try:
        audit_root = tmp_path / "audit"
        sqlite_store = SQLiteAuditStore(audit_root, retention_days=365)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        fresh_ts = datetime.now(timezone.utc).isoformat()
        sqlite_store.append_audit(
            _audit_record(ts=old_ts, msg_id="archived-1", trace_id="t-arch")
        )
        sqlite_store.append_audit(
            _audit_record(ts=fresh_ts, msg_id="live-1", trace_id="t-live")
        )

        archive = PostgresAuditArchiveStore(
            record_store=record_store,
            audit_root=audit_root,
            engine=engine,
        )
        report = archive.archive_files_older_than(7)
        assert report.archived_rows == 1
        assert not (audit_root / f"{old_ts[:10]}.db").exists()
        sqlite_only = sqlite_store.query_audit({"limit": 50})
        assert [r.msg_id for r in sqlite_only] == ["live-1"]
        unioned = sqlite_store.query_audit(
            {
                "limit": 50,
                "include_archive": True,
                "archive_store": archive,
            }
        )
        msg_ids = [r.msg_id for r in unioned]
        assert sorted(msg_ids) == ["archived-1", "live-1"]
        assert msg_ids == sorted(msg_ids, key=lambda _m: 0)  # noop sanity
        assert msg_ids[0] == "archived-1"

        sqlite_store.close()
    finally:
        cleanup()


def test_query_audit_include_archive_dedupes_on_msg_id(tmp_path: Path) -> None:

    engine, record_store, cleanup = _open_postgres_admin_and_scoped()
    try:
        audit_root = tmp_path / "audit"
        sqlite_store = SQLiteAuditStore(audit_root, retention_days=365)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        sqlite_store.append_audit(
            _audit_record(ts=old_ts, msg_id="dup-1", trace_id="t-dup")
        )

        archive = PostgresAuditArchiveStore(
            record_store=record_store,
            audit_root=audit_root,
            engine=engine,
        )
        report = archive.archive_files_older_than(7, keep_files=True)
        assert report.archived_rows == 1
        assert (audit_root / f"{old_ts[:10]}.db").exists()

        unioned = sqlite_store.query_audit(
            {
                "limit": 50,
                "include_archive": True,
                "archive_store": archive,
            }
        )
        assert [r.msg_id for r in unioned] == ["dup-1"]
        sqlite_store.close()
    finally:
        cleanup()


def test_query_audit_without_include_archive_does_not_touch_postgres(
    tmp_path: Path,
) -> None:
    audit_root = tmp_path / "audit"
    sqlite_store = SQLiteAuditStore(audit_root, retention_days=365)
    ts = datetime.now(timezone.utc).isoformat()
    sqlite_store.append_audit(_audit_record(ts=ts, msg_id="solo", trace_id="t-solo"))

    archive = PostgresAuditArchiveStore(
        record_store=_ExplodingStore(),  # type: ignore[arg-type]
        audit_root=audit_root,
    )
    rows = sqlite_store.query_audit({"limit": 10, "archive_store": archive})
    assert [r.msg_id for r in rows] == ["solo"]
    sqlite_store.close()
