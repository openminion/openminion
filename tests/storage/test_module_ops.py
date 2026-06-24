from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from openminion.modules.storage.interfaces import (
    STORAGE_INTERFACE_VERSION,
    ModuleStorageOpsInterface,
    StorageEnvelope,
    StorageError,
    ensure_interface_compatibility,
)
from openminion.modules.storage.migrations.models import (
    BackupArtifact,
    DbState,
    RehydrateReport,
    VerificationReport,
)
from openminion.modules.storage.migrations.module_ids import (
    MODULE_APPLICATION_IDS,
    get_module_application_id,
)
from openminion.modules.storage.runtime.module_ops import (
    StorageModuleOps,
    build_module_ops,
)


def _to_signed32(val: int) -> int:
    import struct

    return struct.unpack(">i", struct.pack(">I", val & 0xFFFFFFFF))[0]


def _create_test_db(
    db_path: Path, *, app_id: int = 0x4F4D0006, user_version: int = 1
) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    signed_app_id = _to_signed32(app_id)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(f"PRAGMA application_id={signed_app_id}")
        conn.execute(f"PRAGMA user_version={user_version}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS secrets (
                key TEXT NOT NULL,
                namespace TEXT NOT NULL DEFAULT 'default',
                value TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (key, namespace)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS om_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO om_meta(key, value) VALUES ('module_id', 'secret')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO om_meta(key, value) VALUES ('schema_head', '0001_baseline')"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS alembic_version(version_num TEXT PRIMARY KEY)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO alembic_version(version_num) VALUES ('0001_baseline')"
        )
        conn.commit()


def _build_ops(tmp_path: Path, *, module_id: str = "secret") -> StorageModuleOps:
    db_path = tmp_path / "test.db"
    app_id = get_module_application_id(module_id)
    _create_test_db(db_path, app_id=app_id)
    return StorageModuleOps(
        module_id=module_id,
        db_path=db_path,
        module_application_id=app_id,
        snapshot_root=tmp_path / "snapshots",
    )


class TestInterfaceCompatibility:
    def test_storage_module_ops_satisfies_protocol(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        ensure_interface_compatibility(ops, interface="module_ops")

    def test_missing_method_fails_compatibility(self) -> None:
        class _Incomplete:
            contract_version = STORAGE_INTERFACE_VERSION
            module_id = "test"
            module_application_id = 0

            def detect(self): ...
            def verify(self, *, level="quick"): ...

        with pytest.raises(TypeError, match="missing required members"):
            ensure_interface_compatibility(_Incomplete(), interface="module_ops")

    def test_module_ops_interface_is_runtime_checkable(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        assert isinstance(ops, ModuleStorageOpsInterface)

    def test_unknown_interface_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown interface"):
            ensure_interface_compatibility(object(), interface="nonexistent")


class TestStorageModuleOps:
    def test_detect_returns_db_state(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        state = ops.detect()
        assert isinstance(state, DbState)
        assert state.exists is True
        assert state.module_id == "secret"
        assert state.application_id_matches is True
        assert state.application_id == get_module_application_id("secret")

    def test_detect_missing_db(self, tmp_path: Path) -> None:
        ops = StorageModuleOps(
            module_id="secret",
            db_path=tmp_path / "nonexistent.db",
            module_application_id=get_module_application_id("secret"),
        )
        state = ops.detect()
        assert state.exists is False
        assert state.application_id_matches is False

    def test_detect_envelope(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        env = ops.detect_envelope()
        assert isinstance(env, StorageEnvelope)
        assert env.ok is True
        assert env.operation == "detect"
        assert env.data["exists"] is True
        assert env.module == "secret"

    def test_verify_quick(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        report = ops.verify(level="quick")
        assert isinstance(report, VerificationReport)
        assert report.ok is True
        assert report.quick_check == "ok"

    def test_verify_full(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        report = ops.verify(level="full")
        assert report.ok is True
        assert report.integrity_check is not None

    def test_verify_envelope(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        env = ops.verify_envelope(level="quick")
        assert env.ok is True
        assert env.operation == "verify"

    def test_backup_creates_snapshot(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        artifact = ops.backup()
        assert isinstance(artifact, BackupArtifact)
        assert Path(artifact.snapshot_path).exists()
        assert artifact.module_id == "secret"

    def test_backup_envelope(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        env = ops.backup_envelope()
        assert env.ok is True
        assert env.operation == "backup"
        assert "snapshot_path" in env.data

    def test_restore_from_backup(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        with sqlite3.connect(str(tmp_path / "test.db")) as conn:
            conn.execute(
                "INSERT INTO secrets(key, namespace, value, created_at, updated_at) "
                "VALUES ('k1', 'default', 'v1', 1.0, 1.0)"
            )
            conn.commit()
        artifact = ops.backup()
        with sqlite3.connect(str(tmp_path / "test.db")) as conn:
            conn.execute("DELETE FROM secrets")
            conn.commit()
        state = ops.restore(snapshot_path=artifact.snapshot_path)
        assert isinstance(state, DbState)
        assert state.exists is True
        with sqlite3.connect(str(tmp_path / "test.db")) as conn:
            row = conn.execute("SELECT value FROM secrets WHERE key='k1'").fetchone()
        assert row is not None
        assert row[0] == "v1"

    def test_restore_envelope(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        artifact = ops.backup()
        env = ops.restore_envelope(snapshot_path=artifact.snapshot_path)
        assert env.ok is True
        assert env.operation == "restore"

    def test_export_creates_omx(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        with sqlite3.connect(str(tmp_path / "test.db")) as conn:
            conn.execute(
                "INSERT INTO secrets(key, namespace, value, created_at, updated_at) "
                "VALUES ('k1', 'default', 'v1', 1.0, 1.0)"
            )
            conn.commit()
        export_dir = tmp_path / "export"
        env = ops.export(export_dir=export_dir)
        assert env.ok is True
        assert env.operation == "export"
        assert (export_dir / "manifest.json").exists()
        manifest = json.loads((export_dir / "manifest.json").read_text())
        assert manifest["module_id"] == "secret"
        assert manifest["module_application_id"] == get_module_application_id("secret")
        assert any(t["name"] == "secrets" for t in manifest["tables"])

    def test_rehydrate_from_export(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        with sqlite3.connect(str(tmp_path / "test.db")) as conn:
            conn.execute(
                "INSERT INTO secrets(key, namespace, value, created_at, updated_at) "
                "VALUES ('k1', 'default', 'v1', 1.0, 1.0)"
            )
            conn.commit()
        export_dir = tmp_path / "export"
        ops.export(export_dir=export_dir)

        target_db = tmp_path / "imported.db"
        report = ops.rehydrate(
            source_db_path=tmp_path / "test.db",
            target_db_path=target_db,
            omx_dir=export_dir,
        )
        assert isinstance(report, RehydrateReport)
        assert report.success is True
        assert report.imported_rows > 0

        with sqlite3.connect(str(target_db)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT value FROM secrets WHERE key='k1'").fetchone()
        assert row is not None
        assert row["value"] == "v1"

    def test_rehydrate_envelope(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        with sqlite3.connect(str(tmp_path / "test.db")) as conn:
            conn.execute(
                "INSERT INTO secrets(key, namespace, value, created_at, updated_at) "
                "VALUES ('k2', 'default', 'v2', 2.0, 2.0)"
            )
            conn.commit()
        export_dir = tmp_path / "export"
        ops.export(export_dir=export_dir)
        target_db = tmp_path / "imported2.db"
        env = ops.rehydrate_envelope(
            source_db_path=tmp_path / "test.db",
            target_db_path=target_db,
            omx_dir=export_dir,
        )
        assert env.ok is True
        assert env.operation == "rehydrate"


class TestBuildModuleOps:
    def test_factory_resolves_app_id(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        _create_test_db(db_path)
        ops = build_module_ops(module_id="secret", db_path=db_path)
        assert ops.module_application_id == get_module_application_id("secret")
        state = ops.detect()
        assert state.exists is True

    def test_factory_unknown_module_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Unknown module_id"):
            build_module_ops(module_id="nonexistent", db_path=tmp_path / "x.db")


class TestModuleIdentityConstants:
    EXPECTED_MODULES = [
        "storage",
        "session",
        "artifact",
        "registry",
        "identity",
        "policy",
        "secret",
        "telemetry",
        "controlplane",
        "controlplane_telegram",
        "retrieve",
        "memory",
        "skill",
        "a2a",
        "compress",
        "task",
    ]

    def test_all_modules_have_application_id(self) -> None:
        for mod in self.EXPECTED_MODULES:
            app_id = get_module_application_id(mod)
            assert isinstance(app_id, int), f"{mod} missing application_id"
            assert app_id > 0, f"{mod} has invalid application_id"

    def test_application_ids_are_unique(self) -> None:
        ids = list(MODULE_APPLICATION_IDS.values())
        assert len(ids) == len(set(ids)), "Duplicate application_ids detected"

    def test_application_ids_follow_naming_convention(self) -> None:
        for mod, app_id in MODULE_APPLICATION_IDS.items():
            assert app_id & 0x4F4D0000 == 0x4F4D0000, (
                f"{mod} application_id {hex(app_id)} does not use OM prefix"
            )

    def test_unknown_module_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown module_id"):
            get_module_application_id("does_not_exist")


class TestIdentityMismatch:
    def test_backup_wrong_app_id_fails(self, tmp_path: Path) -> None:
        db_path = tmp_path / "wrong.db"
        _create_test_db(db_path, app_id=get_module_application_id("session"))
        ops = StorageModuleOps(
            module_id="secret",
            db_path=db_path,
            module_application_id=get_module_application_id("secret"),
            snapshot_root=tmp_path / "snapshots",
        )
        with pytest.raises(Exception, match="application_id mismatch"):
            ops.backup()

    def test_backup_envelope_wrong_app_id(self, tmp_path: Path) -> None:
        db_path = tmp_path / "wrong.db"
        _create_test_db(db_path, app_id=get_module_application_id("session"))
        ops = StorageModuleOps(
            module_id="secret",
            db_path=db_path,
            module_application_id=get_module_application_id("secret"),
            snapshot_root=tmp_path / "snapshots",
        )
        env = ops.backup_envelope()
        assert env.ok is False
        assert env.error is not None
        assert "mismatch" in env.error.message

    def test_mismatch_does_not_mutate_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "protected.db"
        session_app_id = get_module_application_id("session")
        _create_test_db(db_path, app_id=session_app_id)
        with sqlite3.connect(str(db_path)) as conn:
            before_app_id = conn.execute("PRAGMA application_id").fetchone()[0]
            before_version = conn.execute("PRAGMA user_version").fetchone()[0]

        ops = StorageModuleOps(
            module_id="secret",
            db_path=db_path,
            module_application_id=get_module_application_id("secret"),
            snapshot_root=tmp_path / "snapshots",
        )
        env = ops.backup_envelope()
        assert env.ok is False

        with sqlite3.connect(str(db_path)) as conn:
            after_app_id = conn.execute("PRAGMA application_id").fetchone()[0]
            after_version = conn.execute("PRAGMA user_version").fetchone()[0]

        assert after_app_id == before_app_id
        assert after_version == before_version

    def test_detect_reports_mismatch(self, tmp_path: Path) -> None:
        wrong_id = 0x12345678
        db_path = tmp_path / "wrong.db"
        _create_test_db(db_path, app_id=wrong_id)
        ops = StorageModuleOps(
            module_id="secret",
            db_path=db_path,
            module_application_id=get_module_application_id("secret"),
        )
        state = ops.detect()
        assert state.application_id_matches is False
        assert state.application_id == _to_signed32(wrong_id)


class TestSchemaDrift:
    def test_detect_reports_user_version(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        state = ops.detect()
        assert state.user_version == 1

    def test_detect_reports_schema_head_from_om_meta(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        state = ops.detect()
        assert state.om_meta.get("schema_head") == "0001_baseline"
        assert state.alembic_revision == "0001_baseline"

    def test_version_drift_detectable(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        _create_test_db(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("PRAGMA user_version=99")
            conn.commit()

        ops = StorageModuleOps(
            module_id="secret",
            db_path=db_path,
            module_application_id=get_module_application_id("secret"),
        )
        state = ops.detect()
        assert state.user_version == 99
        assert state.om_meta.get("schema_head") == "0001_baseline"  # stale

    def test_detect_envelope_includes_version_info(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        env = ops.detect_envelope()
        assert env.data["user_version"] == 1
        assert env.data["om_meta"]["schema_head"] == "0001_baseline"


class TestRollbackSafety:
    def test_backup_then_restore_preserves_data(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        db_path = tmp_path / "test.db"
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO secrets(key, namespace, value, created_at, updated_at) "
                "VALUES ('preserve_me', 'default', 'safe', 1.0, 1.0)"
            )
            conn.commit()
        artifact = ops.backup()
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("DELETE FROM secrets")
            conn.commit()
        with sqlite3.connect(str(db_path)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM secrets").fetchone()[0] == 0
        ops.restore(snapshot_path=artifact.snapshot_path)
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT value FROM secrets WHERE key='preserve_me'"
            ).fetchone()
        assert row is not None
        assert row[0] == "safe"

    def test_snapshot_not_silently_deleted(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        artifact = ops.backup()
        snapshot = Path(artifact.snapshot_path)
        assert snapshot.exists()
        ops.restore(snapshot_path=snapshot)
        assert snapshot.exists(), "Snapshot was silently deleted after restore"

    def test_restore_is_idempotent(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        artifact = ops.backup()
        ops.restore(snapshot_path=artifact.snapshot_path)
        ops.restore(snapshot_path=artifact.snapshot_path)
        state = ops.detect()
        assert state.exists is True

    def test_restore_nonexistent_snapshot_fails(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        with pytest.raises(Exception):
            ops.restore(snapshot_path=tmp_path / "nonexistent.sqlite")


class TestE2ESmoke:
    def test_full_lifecycle(self, tmp_path: Path) -> None:
        home = tmp_path / "openminion_home"
        data_root = home / ".openminion"
        db_path = data_root / "secret" / "secrets.db"
        snapshot_root = data_root / "secret" / "snapshots"
        export_dir = data_root / "secret" / "export"

        _create_test_db(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO secrets(key, namespace, value, created_at, updated_at) "
                "VALUES ('e2e_key', 'prod', 'e2e_value', 100.0, 100.0)"
            )
            conn.commit()

        ops = StorageModuleOps(
            module_id="secret",
            db_path=db_path,
            module_application_id=get_module_application_id("secret"),
            snapshot_root=snapshot_root,
        )

        state = ops.detect()
        assert state.exists is True
        assert state.application_id_matches is True

        artifact = ops.backup()
        assert Path(artifact.snapshot_path).exists()
        assert str(Path(artifact.snapshot_path).resolve()).startswith(
            str(snapshot_root.resolve())
        )

        report = ops.verify(level="full")
        assert report.ok is True

        env = ops.export(export_dir=export_dir)
        assert env.ok is True
        manifest_path = export_dir / "manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        assert manifest["module_id"] == "secret"

        imported_db = data_root / "secret" / "imported.db"
        rehydrate_report = ops.rehydrate(
            source_db_path=db_path,
            target_db_path=imported_db,
            omx_dir=export_dir,
        )
        assert rehydrate_report.success is True

        with sqlite3.connect(str(imported_db)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT value FROM secrets WHERE key='e2e_key'"
            ).fetchone()
        assert row is not None
        assert row["value"] == "e2e_value"

        for artifact_path in [
            db_path,
            Path(artifact.snapshot_path),
            manifest_path,
            imported_db,
        ]:
            assert str(artifact_path).startswith(str(home)), (
                f"Artifact {artifact_path} escaped temp home {home}"
            )

    def test_full_lifecycle_envelope_mode(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db" / "test.db"
        _create_test_db(db_path)
        ops = StorageModuleOps(
            module_id="secret",
            db_path=db_path,
            module_application_id=get_module_application_id("secret"),
            snapshot_root=tmp_path / "snaps",
        )

        detect_env = ops.detect_envelope()
        assert detect_env.ok is True

        backup_env = ops.backup_envelope()
        assert backup_env.ok is True

        verify_env = ops.verify_envelope(level="quick")
        assert verify_env.ok is True

        export_env = ops.export(export_dir=tmp_path / "export")
        assert export_env.ok is True

        rehydrate_env = ops.rehydrate_envelope(
            source_db_path=db_path,
            target_db_path=tmp_path / "imported.db",
            omx_dir=tmp_path / "export",
        )
        assert rehydrate_env.ok is True

        restore_env = ops.restore_envelope(
            snapshot_path=backup_env.data["snapshot_path"]
        )
        assert restore_env.ok is True

    def test_envelope_error_propagation(self, tmp_path: Path) -> None:
        ops = StorageModuleOps(
            module_id="secret",
            db_path=tmp_path / "missing.db",
            module_application_id=get_module_application_id("secret"),
        )
        backup_env = ops.backup_envelope()
        assert backup_env.ok is False
        assert backup_env.error is not None
        assert isinstance(backup_env.error, StorageError)

        ops.verify_envelope()

    def test_envelope_serialization(self, tmp_path: Path) -> None:
        ops = _build_ops(tmp_path)
        env = ops.detect_envelope()
        payload = env.to_dict()
        json_str = json.dumps(payload)
        parsed = json.loads(json_str)
        assert parsed["operation"] == "detect"
        assert parsed["ok"] is True
        assert parsed["module"] == "secret"
        assert parsed["contract_version"] == STORAGE_INTERFACE_VERSION


class TestCLIOpsWiring:
    def _run_storagectl(self, argv: list[str]) -> dict:
        import io
        import contextlib

        from openminion.modules.storage.cli import main as storagectl_main

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            storagectl_main(argv)
        return json.loads(buf.getvalue())

    def test_status_json(self, tmp_path: Path) -> None:
        db_path = tmp_path / "secrets.db"
        _create_test_db(db_path)
        result = self._run_storagectl(
            ["--namespace", "secret", "--sqlite", str(db_path), "status"]
        )
        assert result["ok"] is True
        assert "status" in result

    def test_verify_json(self, tmp_path: Path) -> None:
        db_path = tmp_path / "secrets.db"
        _create_test_db(db_path)
        result = self._run_storagectl(
            [
                "--namespace",
                "secret",
                "--sqlite",
                str(db_path),
                "verify",
                "--namespace",
                "secret",
                "--sqlite",
                str(db_path),
                "--level",
                "quick",
            ]
        )
        assert "ok" in result
        assert "report" in result

    def test_backup_json(self, tmp_path: Path) -> None:
        db_path = tmp_path / "secrets.db"
        _create_test_db(db_path)
        snap_root = tmp_path / "snaps"
        snap_root.mkdir()
        result = self._run_storagectl(
            [
                "--namespace",
                "secret",
                "--sqlite",
                str(db_path),
                "backup",
                "--namespace",
                "secret",
                "--sqlite",
                str(db_path),
                "--snapshot-root",
                str(snap_root),
            ]
        )
        assert result["ok"] is True
        assert "backup" in result
        assert Path(result["backup"]["snapshot_path"]).exists()

    def test_export_json(self, tmp_path: Path) -> None:
        db_path = tmp_path / "secrets.db"
        _create_test_db(db_path)
        out_dir = tmp_path / "export"
        result = self._run_storagectl(
            [
                "--namespace",
                "secret",
                "--sqlite",
                str(db_path),
                "export",
                "--namespace",
                "secret",
                "--sqlite",
                str(db_path),
                "--out",
                str(out_dir),
            ]
        )
        assert result["ok"] is True
        assert "manifest" in result
        assert (out_dir / "manifest.json").exists()

    def test_backup_restore_round_trip(self, tmp_path: Path) -> None:
        db_path = tmp_path / "secrets.db"
        _create_test_db(db_path)
        snap_root = tmp_path / "snaps"
        snap_root.mkdir()

        backup_result = self._run_storagectl(
            [
                "--namespace",
                "secret",
                "--sqlite",
                str(db_path),
                "backup",
                "--namespace",
                "secret",
                "--sqlite",
                str(db_path),
                "--snapshot-root",
                str(snap_root),
            ]
        )
        snap_path = backup_result["backup"]["snapshot_path"]

        restore_result = self._run_storagectl(
            [
                "--namespace",
                "secret",
                "--sqlite",
                str(db_path),
                "restore",
                "--namespace",
                "secret",
                "--sqlite",
                str(db_path),
                "--snapshot-path",
                snap_path,
            ]
        )
        assert restore_result["ok"] is True
        assert "state" in restore_result
