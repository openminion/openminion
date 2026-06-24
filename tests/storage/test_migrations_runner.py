from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path

import pytest

from openminion.modules.storage.migrations import (
    BACKUP_MODE_ONLINE,
    Finding,
    MigrationRunner,
    export_omx,
    import_omx,
)
from openminion.modules.storage.migrations.module_ids import get_module_application_id
from openminion.modules.secret.storage.store import SQLiteSecretStore

pytestmark = pytest.mark.postgres


MODULE_APP_ID = 0x4F4D0001
SECRET_APP_ID = get_module_application_id("secret")
SESSION_APP_ID = get_module_application_id("session")


def _init_db(path: Path) -> None:
    with sqlite3.connect(str(path)) as conn:
        conn.execute(f"PRAGMA application_id = {MODULE_APP_ID}")
        conn.execute("PRAGMA user_version = 1")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS alembic_version(version_num TEXT PRIMARY KEY)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO alembic_version(version_num) VALUES ('0001_init')"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS om_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO om_meta(key, value) VALUES ('module_id', 'sessctl')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO om_meta(key, value) VALUES ('schema_head', '0001_init')"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, body TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO notes(body) VALUES ('baseline')")
        conn.commit()


def _open_postgres_schema():
    postgres_url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")

    sqlalchemy = pytest.importorskip("sqlalchemy")
    schema_name = f"smbe_runner_{uuid.uuid4().hex}"
    admin_engine = sqlalchemy.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sqlalchemy.text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
    engine = sqlalchemy.create_engine(
        postgres_url,
        future=True,
        connect_args={"options": f"-csearch_path={schema_name}"},
    )
    return sqlalchemy, admin_engine, engine, schema_name


def test_detect_reads_identity_and_meta(tmp_path):
    db_path = tmp_path / "module.db"
    _init_db(db_path)

    runner = MigrationRunner(
        module_id="sessctl",
        db_path=db_path,
        module_application_id=MODULE_APP_ID,
    )

    state = runner.detect()

    assert state.exists is True
    assert state.application_id == MODULE_APP_ID
    assert state.application_id_matches is True
    assert state.user_version == 1
    assert state.alembic_revision == "0001_init"
    assert state.om_meta["module_id"] == "sessctl"


def test_backup_online_creates_snapshot(tmp_path):
    db_path = tmp_path / "module.db"
    _init_db(db_path)

    runner = MigrationRunner(
        module_id="sessctl",
        db_path=db_path,
        module_application_id=MODULE_APP_ID,
        snapshot_root=tmp_path / "snapshots",
        default_backup_mode=BACKUP_MODE_ONLINE,
    )

    artifact = runner.backup()

    snapshot_path = Path(artifact.snapshot_path)
    assert snapshot_path.exists()

    with sqlite3.connect(str(snapshot_path)) as conn:
        row = conn.execute("SELECT body FROM notes LIMIT 1").fetchone()
    assert row is not None
    assert row[0] == "baseline"


def test_verify_quick_reports_ok_for_clean_db(tmp_path):
    db_path = tmp_path / "module.db"
    _init_db(db_path)

    runner = MigrationRunner(
        module_id="sessctl",
        db_path=db_path,
        module_application_id=MODULE_APP_ID,
    )

    report = runner.verify(level="quick")

    assert report.ok is True
    assert report.quick_check == "ok"
    assert report.findings == []


def test_runner_rolls_back_on_failed_verify(tmp_path):
    db_path = tmp_path / "module.db"
    _init_db(db_path)

    runner = MigrationRunner(
        module_id="sessctl",
        db_path=db_path,
        module_application_id=MODULE_APP_ID,
        snapshot_root=tmp_path / "snapshots",
        verifier_hook=lambda _conn: [
            Finding(
                severity="fatal",
                code="forced_failure",
                message="force rollback path",
            )
        ],
    )

    def fake_upgrade(*, target: str) -> None:
        del target
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("INSERT INTO notes(body) VALUES ('changed-during-migration')")
            conn.commit()

    runner._apply_alembic_upgrade = fake_upgrade  # type: ignore[method-assign]

    report = runner.migrate_with_verify(target="head")

    assert report.success is False
    assert report.rolled_back is True
    assert report.error is not None

    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT body FROM notes ORDER BY id").fetchall()

    # rollback should restore snapshot, removing the migration-time row.
    assert [row[0] for row in rows] == ["baseline"]


def test_runner_migrate_with_verify_success(tmp_path):
    db_path = tmp_path / "module.db"
    _init_db(db_path)

    runner = MigrationRunner(
        module_id="sessctl",
        db_path=db_path,
        module_application_id=MODULE_APP_ID,
        snapshot_root=tmp_path / "snapshots",
    )

    def fake_upgrade(*, target: str) -> None:
        del target
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("INSERT INTO notes(body) VALUES ('verified-migration')")
            conn.commit()

    runner._apply_alembic_upgrade = fake_upgrade  # type: ignore[method-assign]

    report = runner.migrate_with_verify(target="head")

    assert report.success is True
    assert report.rolled_back is False
    assert report.error is None

    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT body FROM notes ORDER BY id").fetchall()

    assert [row[0] for row in rows] == ["baseline", "verified-migration"]


def test_secret_migrate_bootstraps_fresh_db_with_alembic(tmp_path: Path) -> None:
    db_path = tmp_path / "secret.db"
    db_path.touch()

    runner = MigrationRunner(
        module_id="secret",
        db_path=db_path,
        module_application_id=SECRET_APP_ID,
    )

    report = runner.migrate(target="head")

    assert report.success is True
    assert report.after.application_id == SECRET_APP_ID
    assert report.after.application_id_matches is True
    assert report.after.alembic_revision == "0001_baseline"
    assert report.after.om_meta["schema_head"] == "0001_baseline"

    with sqlite3.connect(str(db_path)) as conn:
        assert (
            conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "0001_baseline"
        )
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='secrets'"
        ).fetchone()


def test_secret_migrate_existing_db_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "secret-existing.db"
    store = SQLiteSecretStore(db_path)
    store.upsert(
        key="token",
        namespace="default",
        value="cipher",
        created_at=1.0,
        updated_at=1.0,
    )
    store.close()

    runner = MigrationRunner(
        module_id="secret",
        db_path=db_path,
        module_application_id=SECRET_APP_ID,
    )
    report = runner.migrate(target="head")

    assert report.success is True
    assert report.after.alembic_revision == "0001_baseline"

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT value FROM secrets WHERE key = ? AND namespace = ?",
            ("token", "default"),
        ).fetchone()
    assert row is not None
    assert row[0] == "cipher"


def test_secret_backup_restore_round_trip_after_migrate(tmp_path: Path) -> None:
    db_path = tmp_path / "secret-roundtrip.db"
    db_path.touch()
    runner = MigrationRunner(
        module_id="secret",
        db_path=db_path,
        module_application_id=SECRET_APP_ID,
        snapshot_root=tmp_path / "snapshots",
    )
    assert runner.migrate(target="head").success is True

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO secrets(key, namespace, value, created_at, updated_at)
            VALUES ('k1', 'default', 'v1', 1.0, 1.0)
            """
        )
        conn.commit()

    artifact = runner.backup()

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM secrets")
        conn.commit()

    runner.restore(snapshot_path=artifact.snapshot_path)
    verify = runner.verify(level="quick")
    assert verify.ok is True

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT value FROM secrets WHERE key = 'k1'").fetchone()
    assert row is not None
    assert row[0] == "v1"


def test_secret_export_import_round_trip_after_migrate(tmp_path: Path) -> None:
    source_db = tmp_path / "secret-export.db"
    source_db.touch()
    runner = MigrationRunner(
        module_id="secret",
        db_path=source_db,
        module_application_id=SECRET_APP_ID,
    )
    assert runner.migrate(target="head").success is True

    with sqlite3.connect(str(source_db)) as conn:
        conn.execute(
            """
            INSERT INTO secrets(key, namespace, value, created_at, updated_at)
            VALUES ('api', 'default', 'secret', 1.0, 1.0)
            """
        )
        conn.commit()

    export_dir = tmp_path / "omx"
    manifest = export_omx(
        db_path=source_db,
        module_id="secret",
        module_application_id=SECRET_APP_ID,
        export_dir=export_dir,
    )
    assert manifest.module_id == "secret"

    target_db = tmp_path / "secret-import.db"
    report = import_omx(omx_dir=export_dir, target_db_path=target_db)
    assert report.success is True

    imported_runner = MigrationRunner(
        module_id="secret",
        db_path=target_db,
        module_application_id=SECRET_APP_ID,
    )
    assert imported_runner.verify(level="quick").ok is True

    with sqlite3.connect(str(target_db)) as conn:
        row = conn.execute("SELECT value FROM secrets WHERE key = 'api'").fetchone()
    assert row is not None
    assert row[0] == "secret"


@pytest.mark.postgres
def test_postgres_migrate_secret_bootstraps_schema() -> None:
    sqlalchemy, admin_engine, engine, schema_name = _open_postgres_schema()
    try:
        runner = MigrationRunner(
            module_id="secret",
            db_path="postgres://secret",
            module_application_id=SECRET_APP_ID,
            backend_type="postgres",
            engine=engine,
        )

        report = runner.migrate(target="head")

        assert report.success is True
        assert report.backup.mode == "transactional_ddl"
        assert report.backup.snapshot_path == ""
        assert report.after.alembic_revision == "0001_baseline"
        assert report.after.om_meta["schema_head"] == "0001_baseline"
        assert report.after.application_id is None
        with engine.connect() as conn:
            version = conn.execute(
                sqlalchemy.text("SELECT version_num FROM alembic_version")
            ).scalar()
        assert version == "0001_baseline"
    finally:
        try:
            engine.dispose()
        finally:
            with admin_engine.begin() as conn:
                conn.execute(
                    sqlalchemy.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
                )
            admin_engine.dispose()


@pytest.mark.postgres
def test_postgres_migrate_rolls_back_failed_transaction() -> None:
    sqlalchemy, admin_engine, engine, schema_name = _open_postgres_schema()
    try:
        runner = MigrationRunner(
            module_id="secret",
            db_path="postgres://secret",
            module_application_id=SECRET_APP_ID,
            backend_type="postgres",
            engine=engine,
        )

        def fake_upgrade(*, target: str, connection) -> None:
            del target
            connection.execute(
                sqlalchemy.text(
                    "CREATE TABLE temp_failed_migration(id INTEGER PRIMARY KEY)"
                )
            )
            raise RuntimeError("boom")

        runner._apply_alembic_upgrade = fake_upgrade  # type: ignore[method-assign]
        report = runner.migrate(target="head")

        assert report.success is False
        assert report.error is not None
        inspector = sqlalchemy.inspect(engine)
        assert inspector.has_table("temp_failed_migration") is False
    finally:
        try:
            engine.dispose()
        finally:
            with admin_engine.begin() as conn:
                conn.execute(
                    sqlalchemy.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
                )
            admin_engine.dispose()


@pytest.mark.postgres
def test_postgres_migrate_session_bootstraps_schema() -> None:
    sqlalchemy, admin_engine, engine, schema_name = _open_postgres_schema()
    try:
        runner = MigrationRunner(
            module_id="session",
            db_path="postgres://session",
            module_application_id=SESSION_APP_ID,
            backend_type="postgres",
            engine=engine,
        )

        report = runner.migrate(target="head")

        assert report.success is True
        assert report.backup.mode == "transactional_ddl"
        assert report.after.alembic_revision == "0001_baseline"
        assert report.after.om_meta["schema_head"] == "0001_baseline"
        with engine.connect() as conn:
            version = conn.execute(
                sqlalchemy.text("SELECT version_num FROM alembic_version")
            ).scalar()
            sessions_table = conn.execute(
                sqlalchemy.text(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.tables
                    WHERE table_schema = current_schema()
                      AND table_name = 'sessions'
                    """
                )
            ).scalar()
        assert version == "0001_baseline"
        assert int(sessions_table or 0) == 1
    finally:
        try:
            engine.dispose()
        finally:
            with admin_engine.begin() as conn:
                conn.execute(
                    sqlalchemy.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
                )
            admin_engine.dispose()
