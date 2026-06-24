from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from openminion.modules.storage.drill import run_drill
from openminion.modules.storage.migrations import (
    BACKUP_MODE_ONLINE,
    MigrationRunner,
)


MODULE_APP_ID = 0x4F4D0001


def _init_db(path: Path, *, body: str = "baseline") -> None:
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
        conn.execute("INSERT INTO notes(body) VALUES (?)", (body,))
        conn.commit()


def test_dr_drill_succeeds_end_to_end(tmp_path: Path) -> None:
    db_path = tmp_path / "module.db"
    _init_db(db_path, body="drill-source")

    runner = MigrationRunner(
        module_id="sessctl",
        db_path=db_path,
        module_application_id=MODULE_APP_ID,
        snapshot_root=tmp_path / "snapshots",
        default_backup_mode=BACKUP_MODE_ONLINE,
    )
    target = tmp_path / "restored" / "sessctl.restored.db"

    report = run_drill(runner=runner, target_path=target, verify_level="quick")

    assert report.ok is True
    assert report.error is None
    assert Path(report.snapshot_path).exists()
    assert Path(report.restore_target_path).exists()
    assert report.source_sha256 is not None
    assert report.restore_duration_seconds >= 0.0
    assert report.verification is not None
    assert report.verification.ok is True

    # Round-trip: restored DB carries the source row.
    with sqlite3.connect(str(target)) as conn:
        row = conn.execute("SELECT body FROM notes LIMIT 1").fetchone()
    assert row is not None
    assert row[0] == "drill-source"


def test_dr_drill_to_dict_round_trips_as_json(tmp_path: Path) -> None:
    db_path = tmp_path / "module.db"
    _init_db(db_path)

    runner = MigrationRunner(
        module_id="sessctl",
        db_path=db_path,
        module_application_id=MODULE_APP_ID,
        snapshot_root=tmp_path / "snapshots",
        default_backup_mode=BACKUP_MODE_ONLINE,
    )
    report = run_drill(
        runner=runner,
        target_path=tmp_path / "restored.db",
        verify_level="quick",
    )

    payload = report.to_dict()
    # Ensure it survives a JSON round trip (i.e. no dataclass leaks).
    encoded = json.dumps(payload)
    decoded = json.loads(encoded)
    assert decoded["module_id"] == "sessctl"
    assert decoded["ok"] is True
    assert decoded["verification"]["ok"] is True
    assert decoded["backup_artifact"]["mode"] == BACKUP_MODE_ONLINE


def test_dr_drill_rejects_directory_target(tmp_path: Path) -> None:
    db_path = tmp_path / "module.db"
    _init_db(db_path)
    runner = MigrationRunner(
        module_id="sessctl",
        db_path=db_path,
        module_application_id=MODULE_APP_ID,
        snapshot_root=tmp_path / "snapshots",
        default_backup_mode=BACKUP_MODE_ONLINE,
    )

    bad_target = tmp_path / "dir_target"
    bad_target.mkdir()

    import pytest

    from openminion.modules.storage.migrations.errors import StorageMigrationError

    with pytest.raises(StorageMigrationError):
        run_drill(runner=runner, target_path=bad_target)
