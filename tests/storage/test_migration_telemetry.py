from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from openminion.modules.storage.migrations.models import Finding
from openminion.modules.storage.migrations.runner import MigrationRunner
from openminion.modules.telemetry.storage.hook import (
    MIGRATION_EVENT_TYPE,
    TelemetryServiceStorageHook,
)


MODULE_APP_ID = 0x4F4D0001


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


class _RecordingMigrationHook:
    def __init__(self) -> None:
        self.starts: list[tuple[str, str]] = []
        self.ends: list[tuple[str, str, float, bool, str | None]] = []

    def on_pool_stats(self, stats):  # pragma: no cover - not used here
        del stats

    def on_query_start(self, sql, params):  # pragma: no cover - not used here
        del sql, params
        return None

    def on_query_end(
        self, token, duration_ms, error
    ):  # pragma: no cover - not used here
        del token, duration_ms, error

    def on_slow_query(
        self, sql, duration_ms, threshold_ms
    ):  # pragma: no cover - not used here
        del sql, duration_ms, threshold_ms

    def on_migration_start(self, module_id: str, operation: str) -> tuple[str, str]:
        self.starts.append((module_id, operation))
        return module_id, operation

    def on_migration_end(
        self,
        token,
        module_id: str,
        operation: str,
        duration_ms: float,
        success: bool,
        error: str | None,
    ) -> None:
        assert token == (module_id, operation)
        self.ends.append((module_id, operation, duration_ms, success, error))


class _RecordingTelemetryService:
    def __init__(self) -> None:
        self.events: list[object] = []

    def record_event_sync(self, event: object) -> None:
        self.events.append(event)


def _runner(tmp_path: Path, hook: _RecordingMigrationHook) -> MigrationRunner:
    db_path = tmp_path / "module.db"
    _init_db(db_path)
    return MigrationRunner(
        module_id="sessctl",
        db_path=db_path,
        module_application_id=MODULE_APP_ID,
        snapshot_root=tmp_path / "snapshots",
        telemetry_hook=hook,
    )


def _end_for_operation(
    hook: _RecordingMigrationHook, operation: str
) -> tuple[str, str, float, bool, str | None]:
    for item in reversed(hook.ends):
        if item[1] == operation:
            return item
    raise AssertionError(f"operation {operation!r} not found in {hook.ends!r}")


def test_backup_emits_start_and_end(tmp_path: Path) -> None:
    hook = _RecordingMigrationHook()
    runner = _runner(tmp_path, hook)
    artifact = runner.backup()
    assert artifact.snapshot_path
    assert hook.starts == [("sessctl", "backup")]
    assert len(hook.ends) == 1
    module_id, operation, duration_ms, success, error = hook.ends[0]
    assert (module_id, operation) == ("sessctl", "backup")
    assert duration_ms >= 0.0
    assert success is True
    assert error is None


def test_restore_emits_start_and_end(tmp_path: Path) -> None:
    hook = _RecordingMigrationHook()
    runner = _runner(tmp_path, hook)
    artifact = runner.backup()
    restored = tmp_path / "restored.db"
    runner.restore(snapshot_path=artifact.snapshot_path, target_db_path=restored)
    assert restored.exists()
    assert hook.starts[-1] == ("sessctl", "restore")
    assert hook.ends[-1][1] == "restore"
    assert hook.ends[-1][3] is True
    assert hook.ends[-1][4] is None


def test_verify_emits_start_and_end(tmp_path: Path) -> None:
    hook = _RecordingMigrationHook()
    runner = _runner(tmp_path, hook)
    report = runner.verify(level="quick")
    assert report.ok is True
    assert hook.starts[-1] == ("sessctl", "verify")
    assert hook.ends[-1][1] == "verify"
    assert hook.ends[-1][3] is True


def test_migrate_emits_start_and_end(tmp_path: Path) -> None:
    hook = _RecordingMigrationHook()
    runner = _runner(tmp_path, hook)

    def fake_upgrade(*, target: str) -> None:
        del target
        with sqlite3.connect(str(runner.db_path)) as conn:
            conn.execute("INSERT INTO notes(body) VALUES ('migrated')")
            conn.commit()

    runner._apply_alembic_upgrade = fake_upgrade  # type: ignore[method-assign]
    report = runner.migrate(target="head")
    assert report.success is True
    assert ("sessctl", "migrate") in hook.starts
    end = _end_for_operation(hook, "migrate")
    assert end[3] is True


def test_migrate_with_verify_emits_failed_outcome_without_exception(
    tmp_path: Path,
) -> None:
    hook = _RecordingMigrationHook()
    runner = _runner(tmp_path, hook)
    runner = MigrationRunner(
        module_id="sessctl",
        db_path=runner.db_path,
        module_application_id=MODULE_APP_ID,
        snapshot_root=tmp_path / "snapshots",
        telemetry_hook=hook,
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
        with sqlite3.connect(str(runner.db_path)) as conn:
            conn.execute("INSERT INTO notes(body) VALUES ('changed-during-migration')")
            conn.commit()

    runner._apply_alembic_upgrade = fake_upgrade  # type: ignore[method-assign]
    report = runner.migrate_with_verify(target="head")
    assert report.success is False
    assert ("sessctl", "migrate_with_verify") in hook.starts
    end = _end_for_operation(hook, "migrate_with_verify")
    assert end[3] is False
    assert end[4]


def test_fallback_rehydrate_emits_start_and_end(tmp_path: Path, monkeypatch) -> None:
    hook = _RecordingMigrationHook()
    runner = _runner(tmp_path, hook)

    def fake_import_omx(*, omx_dir, target_db_path):
        del omx_dir, target_db_path
        return SimpleNamespace(success=True, error=None)

    monkeypatch.setattr(
        "openminion.modules.storage.migrations.transfer.import_omx",
        fake_import_omx,
    )
    report = runner.fallback_rehydrate(
        source_db_path=tmp_path / "source.db",
        target_db_path=tmp_path / "target.db",
        omx_dir=tmp_path / "omx",
    )
    assert report.success is True
    assert hook.starts[-1] == ("sessctl", "fallback_rehydrate")
    assert hook.ends[-1][1] == "fallback_rehydrate"
    assert hook.ends[-1][3] is True


def test_adapter_bridges_migration_events() -> None:
    service = _RecordingTelemetryService()
    adapter = TelemetryServiceStorageHook(service)
    token = adapter.on_migration_start("memory", "migrate")
    adapter.on_migration_end(
        token,
        "memory",
        "migrate",
        12.5,
        True,
        None,
    )
    assert len(service.events) == 1
    event = service.events[0]
    assert event.event_type == MIGRATION_EVENT_TYPE
    assert event.data["module_id"] == "memory"
    assert event.data["operation"] == "migrate"
    assert event.data["duration_ms"] == 12.5
    assert event.data["success"] is True
    assert event.data["error"] is None
