from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from openminion.modules.storage.migrations.transfer import (
    export_omx,
    import_omx,
)
from openminion.modules.storage.record_store import RecordStore, RecordStoreSQLite

pytestmark = pytest.mark.postgres


# Helpers


def _build_widgets_schema(store: RecordStore) -> None:

    store.execute_count(
        'CREATE TABLE "widgets" ('
        '"id" INTEGER PRIMARY KEY, '
        '"name" TEXT NOT NULL, '
        '"weight" INTEGER NOT NULL'
        ")"
    )


def _seed_widgets(store: RecordStore, count: int = 5) -> list[dict]:
    rows = [
        {"id": i, "name": f"widget-{i}", "weight": i * 10} for i in range(1, count + 1)
    ]
    store.insert_many("widgets", rows)
    return rows


def _read_all_widgets(store: RecordStore) -> list[dict]:
    return store.query_dicts(
        'SELECT "id", "name", "weight" FROM "widgets" ORDER BY "id"'
    )


def _open_postgres_pair():

    postgres_url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")

    try:
        from sqlalchemy import create_engine
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"sqlalchemy unavailable for postgres backend: {exc}")
    try:
        from openminion.modules.storage.backends.postgres import (
            RecordStorePostgres,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"postgres backend unavailable: {exc}")

    schema_name = f"smp04_{uuid.uuid4().hex}"
    admin_engine = create_engine(postgres_url, future=True)
    admin_store = RecordStorePostgres(admin_engine)
    scoped_engine = create_engine(
        postgres_url,
        future=True,
        connect_args={"options": f"-csearch_path={schema_name}"},
    )
    scoped_store = RecordStorePostgres(scoped_engine)
    admin_store.execute_count(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')

    def _cleanup() -> None:
        for close_fn in (
            getattr(scoped_store, "close", None),
            getattr(admin_store, "close", None),
        ):
            try:
                if callable(close_fn):
                    close_fn()
            except Exception:  # noqa: BLE001
                pass
        try:
            admin_engine.dispose()
        except Exception:  # noqa: BLE001
            pass
        try:
            scoped_engine.dispose()
        except Exception:  # noqa: BLE001
            pass
        try:
            cleanup_engine = create_engine(postgres_url, future=True)
            cleanup_store = RecordStorePostgres(cleanup_engine)
            cleanup_store.execute_count(
                f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'
            )
            cleanup_store.close()
            cleanup_engine.dispose()
        except Exception:  # noqa: BLE001
            pass

    return admin_store, schema_name, scoped_engine, scoped_store, _cleanup


# SQLite-only roundtrip (always exercised)


def test_sqlite_record_store_roundtrip(tmp_path: Path) -> None:

    src_path = tmp_path / "src.db"
    tgt_path = tmp_path / "tgt.db"
    bundle = tmp_path / "omx"

    src = RecordStoreSQLite(src_path)
    tgt = RecordStoreSQLite(tgt_path)
    try:
        _build_widgets_schema(src)
        _build_widgets_schema(tgt)
        seeded = _seed_widgets(src, count=10)

        manifest = export_omx(
            record_store=src,
            module_id="task",
            module_application_id=42,
            export_dir=bundle,
        )
        assert manifest.module_id == "task"
        assert {t.name for t in manifest.tables} >= {"widgets"}
        widgets_entry = next(t for t in manifest.tables if t.name == "widgets")
        assert widgets_entry.row_count == len(seeded)

        report = import_omx(
            omx_dir=bundle,
            target_record_store=tgt,
            verify_checksums=True,
        )
        assert report.success is True, report.error
        assert report.imported_rows == len(seeded)

        landed = _read_all_widgets(tgt)
        assert landed == seeded
    finally:
        src.close()
        tgt.close()


def test_sqlite_path_to_sqlite_record_store_roundtrip(tmp_path: Path) -> None:

    src_path = tmp_path / "src.db"
    tgt_path = tmp_path / "tgt.db"
    bundle = tmp_path / "omx"

    src = RecordStoreSQLite(src_path)
    tgt = RecordStoreSQLite(tgt_path)
    try:
        _build_widgets_schema(src)
        _build_widgets_schema(tgt)
        seeded = _seed_widgets(src, count=4)
        src.close()  # release the path-based export's exclusive read

        manifest = export_omx(
            db_path=src_path,
            module_id="task",
            module_application_id=42,
            export_dir=bundle,
        )
        assert manifest.tables  # not empty

        report = import_omx(
            omx_dir=bundle,
            target_record_store=tgt,
            verify_checksums=True,
        )
        assert report.success is True, report.error
        landed = _read_all_widgets(tgt)
        assert landed == seeded
    finally:
        try:
            src.close()
        except Exception:  # noqa: BLE001
            pass
        tgt.close()


# Cross-backend roundtrips (Postgres-gated)


def test_sqlite_to_postgres_roundtrip(tmp_path: Path) -> None:

    _admin, _schema, _engine, pg_store, cleanup = _open_postgres_pair()
    try:
        src_path = tmp_path / "src.db"
        bundle = tmp_path / "omx"
        src = RecordStoreSQLite(src_path)
        try:
            _build_widgets_schema(src)
            seeded = _seed_widgets(src, count=12)
            export_omx(
                record_store=src,
                module_id="task",
                module_application_id=42,
                export_dir=bundle,
            )
        finally:
            src.close()

        # Postgres needs the target schema to exist before insert_many.
        _build_widgets_schema(pg_store)

        report = import_omx(
            omx_dir=bundle,
            target_record_store=pg_store,
            verify_checksums=True,
        )
        assert report.success is True, report.error
        assert report.imported_rows == len(seeded)

        landed = _read_all_widgets(pg_store)
        assert landed == seeded
    finally:
        cleanup()


def test_postgres_to_sqlite_roundtrip(tmp_path: Path) -> None:

    _admin, _schema, _engine, pg_store, cleanup = _open_postgres_pair()
    try:
        _build_widgets_schema(pg_store)
        seeded = _seed_widgets(pg_store, count=8)

        bundle = tmp_path / "omx"
        export_omx(
            record_store=pg_store,
            module_id="task",
            module_application_id=42,
            export_dir=bundle,
        )

        tgt_path = tmp_path / "tgt.db"
        tgt = RecordStoreSQLite(tgt_path)
        try:
            _build_widgets_schema(tgt)
            report = import_omx(
                omx_dir=bundle,
                target_record_store=tgt,
                verify_checksums=True,
            )
            assert report.success is True, report.error
            landed = _read_all_widgets(tgt)
            assert landed == seeded
        finally:
            tgt.close()
    finally:
        cleanup()
