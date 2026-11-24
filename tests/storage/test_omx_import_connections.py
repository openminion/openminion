from __future__ import annotations

import gc
import os
import sqlite3
from pathlib import Path

import pytest

from openminion.modules.storage.migrations import (
    MigrationRunner,
    export_omx,
    import_omx,
)
from openminion.modules.storage.migrations import transfer
from openminion.modules.storage.migrations.module_ids import get_module_application_id

SECRET_APP_ID = get_module_application_id("secret")


def _exported_secret_bundle(tmp_path: Path) -> Path:
    source_db = tmp_path / "secret-source.db"
    source_db.touch()
    runner = MigrationRunner(
        module_id="secret",
        db_path=source_db,
        module_application_id=SECRET_APP_ID,
    )
    assert runner.migrate(target="head").success is True
    conn = sqlite3.connect(str(source_db))
    try:
        conn.execute(
            """
            INSERT INTO secrets(key, namespace, value, created_at, updated_at)
            VALUES ('api', 'default', 'secret', 1.0, 1.0)
            """
        )
        conn.commit()
    finally:
        conn.close()
    export_dir = tmp_path / "omx"
    export_omx(
        db_path=source_db,
        module_id="secret",
        module_application_id=SECRET_APP_ID,
        export_dir=export_dir,
    )
    return export_dir


def _track_connections(monkeypatch: pytest.MonkeyPatch) -> list[sqlite3.Connection]:
    # Strong references: an unclosed connection stays open (not collected) until asserted.
    opened: list[sqlite3.Connection] = []
    connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        conn = connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)
    return opened


def _is_open(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return False
    return True


def _secret_values(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return [row[0] for row in conn.execute("SELECT value FROM secrets")]
    finally:
        conn.close()


def test_import_omx_closes_every_sqlite_connection_it_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export_dir = _exported_secret_bundle(tmp_path)
    target_db = tmp_path / "secret-import.db"
    opened = _track_connections(monkeypatch)

    report = import_omx(omx_dir=export_dir, target_db_path=target_db)

    assert report.success is True
    assert opened
    assert [_is_open(conn) for conn in opened] == [False] * len(opened)
    monkeypatch.undo()
    assert _secret_values(target_db) == ["secret"]


def test_import_omx_rolls_back_and_closes_when_a_table_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export_dir = _exported_secret_bundle(tmp_path)
    target_db = tmp_path / "secret-import.db"
    replace_rows = transfer._replace_sqlite_table_rows

    def failing_replace(**kwargs):
        replace_rows(**kwargs)
        if kwargs["quoted_table"] == '"secrets"':
            raise RuntimeError("forced table import failure")
        return 0

    monkeypatch.setattr(transfer, "_replace_sqlite_table_rows", failing_replace)
    opened = _track_connections(monkeypatch)

    report = import_omx(omx_dir=export_dir, target_db_path=target_db)

    assert report.success is False
    assert report.error == "forced table import failure"
    assert len(opened) == 1
    assert _is_open(opened[0]) is False
    monkeypatch.undo()
    assert _secret_values(target_db) == []


def test_imported_database_can_be_renamed_after_import_omx_without_gc(
    tmp_path: Path,
) -> None:
    export_dir = _exported_secret_bundle(tmp_path)
    target_db = tmp_path / "secret-import.db"
    moved = tmp_path / "secret-import.moved.db"

    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        assert import_omx(omx_dir=export_dir, target_db_path=target_db).success is True
        # On Windows an open handle makes this raise PermissionError (WinError 32).
        os.replace(target_db, moved)
    finally:
        if gc_was_enabled:
            gc.enable()

    assert moved.is_file()
