from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def _make_args(**kwargs):
    class Args:
        pass

    a = Args()
    defaults = {
        "sqlite": "",
        "backend": "sqlite",
        "postgres_url": "",
        "module": None,
        "plan": False,
        "verify": False,
        "json": False,
        "output": None,
        "snapshot": None,
        "yes": False,
    }
    defaults.update(kwargs)
    for k, v in defaults.items():
        setattr(a, k, v)
    return a


class TestStorageMigrateHelpers:
    def test_get_validated_modules_sqlite(self):
        from openminion.cli.commands.storage import _get_validated_module_ids

        to_run, skipped = _get_validated_module_ids("sqlite", None)
        assert len(to_run) > 0
        assert len(skipped) == 0
        assert "session" in to_run
        assert "secret" in to_run

    def test_get_validated_modules_postgres(self):
        from openminion.cli.commands.storage import _get_validated_module_ids

        to_run, skipped = _get_validated_module_ids("postgres", None)
        assert "secret" in to_run
        assert "session" in to_run
        assert "memory" in to_run
        assert "storage" in skipped

    def test_single_module_sqlite(self):
        from openminion.cli.commands.storage import _get_validated_module_ids

        to_run, skipped = _get_validated_module_ids("sqlite", "secret")
        assert to_run == ["secret"]
        assert skipped == []

    def test_single_module_postgres_valid(self):
        from openminion.cli.commands.storage import _get_validated_module_ids

        to_run, skipped = _get_validated_module_ids("postgres", "session")
        assert to_run == ["session"]

    def test_single_module_postgres_invalid_raises(self):
        from openminion.cli.commands.storage import _get_validated_module_ids

        with pytest.raises(SystemExit):
            _get_validated_module_ids("postgres", "storage")

    def test_unknown_module_raises(self):
        from openminion.cli.commands.storage import _get_validated_module_ids

        with pytest.raises(SystemExit):
            _get_validated_module_ids("sqlite", "nonexistent_module")


class TestStorageMigratePlan:
    def test_plan_calls_detect(self, tmp_path, capsys):
        from openminion.cli.commands.storage import run_storage_migrate

        db = str(tmp_path / "test.db")

        mock_state = MagicMock()
        mock_state.alembic_revision = None

        mock_runner = MagicMock()
        mock_runner.detect.return_value = mock_state

        with patch(
            "openminion.cli.commands.storage._make_runner", return_value=mock_runner
        ):
            args = _make_args(sqlite=db, plan=True, module="secret")
            run_storage_migrate(args)

        mock_runner.detect.assert_called_once()
        mock_runner.migrate.assert_not_called()

    def test_plan_json_output(self, tmp_path, capsys):
        from openminion.cli.commands.storage import run_storage_migrate

        db = str(tmp_path / "test.db")

        mock_state = MagicMock()
        mock_state.alembic_revision = "abc123"

        mock_runner = MagicMock()
        mock_runner.detect.return_value = mock_state

        with patch(
            "openminion.cli.commands.storage._make_runner", return_value=mock_runner
        ):
            args = _make_args(sqlite=db, plan=True, module="secret", json=True)
            run_storage_migrate(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "modules" in data
        assert data["modules"][0]["module_id"] == "secret"


class TestStorageMigrateApply:
    def test_migrate_calls_runner(self, tmp_path):
        from openminion.cli.commands.storage import run_storage_migrate

        db = str(tmp_path / "test.db")

        mock_report = MagicMock()
        mock_report.success = True
        mock_report.applied_versions = ["0001"]

        mock_runner = MagicMock()
        mock_runner.migrate.return_value = mock_report

        with patch(
            "openminion.cli.commands.storage._make_runner", return_value=mock_runner
        ):
            args = _make_args(sqlite=db, module="secret")
            run_storage_migrate(args)

        mock_runner.migrate.assert_called_once_with(target="head")

    def test_migrate_verify_calls_combined_runner(self, tmp_path):
        from openminion.cli.commands.storage import run_storage_migrate

        db = str(tmp_path / "test.db")

        mock_report = MagicMock()
        mock_report.success = True
        mock_report.applied_versions = ["0001"]

        mock_runner = MagicMock()
        mock_runner.migrate_with_verify.return_value = mock_report

        with patch(
            "openminion.cli.commands.storage._make_runner", return_value=mock_runner
        ):
            args = _make_args(sqlite=db, module="secret", verify=True)
            run_storage_migrate(args)

        mock_runner.migrate_with_verify.assert_called_once_with(target="head")
        mock_runner.migrate.assert_not_called()

    def test_migrate_failure_exits_1(self, tmp_path):
        from openminion.cli.commands.storage import run_storage_migrate

        db = str(tmp_path / "test.db")

        mock_runner = MagicMock()
        mock_runner.migrate.side_effect = RuntimeError("migration failed")

        with patch(
            "openminion.cli.commands.storage._make_runner", return_value=mock_runner
        ):
            args = _make_args(sqlite=db, module="secret")
            with pytest.raises(SystemExit) as exc:
                run_storage_migrate(args)
            assert exc.value.code == 1

    def test_postgres_runs_all_validated_modules(self, tmp_path, capsys):
        from openminion.cli.commands.storage import run_storage_migrate

        mock_report = MagicMock()
        mock_report.success = True
        mock_report.applied_versions = []
        mock_runner = MagicMock()
        mock_runner.migrate.return_value = mock_report

        mock_engine = MagicMock()

        with patch(
            "openminion.cli.commands.storage._make_runner", return_value=mock_runner
        ):
            with patch(
                "openminion.cli.commands.storage._get_postgres_engine",
                return_value=mock_engine,
            ):
                args = _make_args(
                    backend="postgres",
                    postgres_url="postgresql://localhost/test",
                    json=True,
                )
                run_storage_migrate(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)

        migrated = [m for m in data["modules"] if m["status"] in ("ok", "error")]
        skipped = [m for m in data["modules"] if m["status"] == "skipped"]

        assert len(migrated) == 14
        assert any(item["module_id"] == "storage" for item in skipped)


class TestStorageVerify:
    def test_verify_calls_runner(self, tmp_path):
        from openminion.cli.commands.storage import run_storage_verify

        db = str(tmp_path / "test.db")

        mock_report = MagicMock()
        mock_report.passed = True
        mock_runner = MagicMock()
        mock_runner.verify.return_value = mock_report

        with patch(
            "openminion.cli.commands.storage._make_runner", return_value=mock_runner
        ):
            args = _make_args(sqlite=db, module="secret")
            run_storage_verify(args)

        mock_runner.verify.assert_called_once()

    def test_verify_failure_exits_1(self, tmp_path):
        from openminion.cli.commands.storage import run_storage_verify

        db = str(tmp_path / "test.db")

        mock_report = MagicMock()
        mock_report.passed = False
        mock_runner = MagicMock()
        mock_runner.verify.return_value = mock_report

        with patch(
            "openminion.cli.commands.storage._make_runner", return_value=mock_runner
        ):
            args = _make_args(sqlite=db, module="secret")
            with pytest.raises(SystemExit) as exc:
                run_storage_verify(args)
            assert exc.value.code == 1


class TestStorageBackup:
    def test_sqlite_backup_creates_file(self, tmp_path):
        from openminion.cli.commands.storage import run_storage_backup

        db = str(tmp_path / "test.db")
        out = str(tmp_path / "backup.db")

        snapshot_path = str(tmp_path / "snap.db")
        Path(snapshot_path).write_bytes(b"fake snapshot")

        mock_artifact = MagicMock()
        mock_artifact.snapshot_path = snapshot_path
        mock_runner = MagicMock()
        mock_runner.backup.return_value = mock_artifact

        with patch(
            "openminion.cli.commands.storage._make_runner", return_value=mock_runner
        ):
            args = _make_args(sqlite=db, backend="sqlite", output=out)
            run_storage_backup(args)

        assert Path(out).exists()

    def test_postgres_backup_prints_guidance_no_url(self, capsys):
        from openminion.cli.commands.storage import run_storage_backup

        args = _make_args(backend="postgres")
        run_storage_backup(args)
        captured = capsys.readouterr()
        # Must reference env var, not print any URL value
        assert "OPENMINION_STORAGE_POSTGRES_URL" in captured.out
        assert "pg_dump" in captured.out

    def test_postgres_backup_no_credentials_in_output(self, capsys):
        from openminion.cli.commands.storage import run_storage_backup

        args = _make_args(backend="postgres")
        args.postgres_url = "postgresql://user:SuperSecret@host/db"
        run_storage_backup(args)
        captured = capsys.readouterr()
        assert "SuperSecret" not in captured.out


class TestStorageRestore:
    def test_restore_requires_snapshot(self):
        from openminion.cli.commands.storage import run_storage_restore

        args = _make_args(snapshot=None, sqlite="/tmp/db.db", yes=True)
        with pytest.raises(SystemExit):
            run_storage_restore(args)

    def test_postgres_restore_prints_guidance(self, capsys):
        from openminion.cli.commands.storage import run_storage_restore

        args = _make_args(backend="postgres", snapshot="/tmp/backup.dump", yes=True)
        run_storage_restore(args)
        captured = capsys.readouterr()
        assert "pg_restore" in captured.out
        assert "OPENMINION_STORAGE_POSTGRES_URL" in captured.out

    def test_sqlite_restore_with_yes(self, tmp_path):
        from openminion.cli.commands.storage import run_storage_restore

        db = str(tmp_path / "test.db")
        snap = str(tmp_path / "snap.db")

        mock_runner = MagicMock()

        with patch(
            "openminion.cli.commands.storage._make_runner", return_value=mock_runner
        ):
            args = _make_args(sqlite=db, snapshot=snap, yes=True)
            run_storage_restore(args)

        mock_runner.restore.assert_called_once()
