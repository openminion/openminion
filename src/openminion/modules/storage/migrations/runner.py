from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from contextlib import contextmanager

from openminion.modules.storage.migrations.alembic import (
    discover_module_alembic_paths,
)
from openminion.modules.storage.migrations.backup import (
    BACKUP_MODE_ONLINE,
    create_snapshot,
    restore_snapshot,
)
from openminion.modules.storage.migrations.errors import (
    DbIdentityError,
    MigrationApplyError,
    RehydrateError,
    VerificationError,
)
from openminion.modules.storage.migrations.models import (
    BackupArtifact,
    DbState,
    MigrationReport,
    RehydrateReport,
)
from openminion.modules.storage.migrations.verify import VerifierHook, run_verification
from openminion.modules.storage.telemetry import (
    NoopStorageTelemetryHook,
    StorageTelemetryHook,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection as SAConnection
    from sqlalchemy.engine import Engine


class MigrationRunner:
    """Shared migration orchestration for module-owned databases."""

    def __init__(
        self,
        *,
        module_id: str,
        db_path: str | Path,
        module_application_id: int,
        snapshot_root: str | Path | None = None,
        alembic_ini_path: str | Path | None = None,
        alembic_script_location: str | Path | None = None,
        default_backup_mode: str = BACKUP_MODE_ONLINE,
        sqlite3_bin: str = "sqlite3",
        target_user_version: int | None = None,
        verifier_hook: VerifierHook | None = None,
        backend_type: str = "sqlite",
        engine: Engine | None = None,
        telemetry_hook: StorageTelemetryHook | None = None,
    ) -> None:
        self.module_id = str(module_id).strip()
        self.db_path = Path(db_path).expanduser().resolve(strict=False)
        self.module_application_id = int(module_application_id)
        self.snapshot_root = (
            Path(snapshot_root).expanduser().resolve(strict=False)
            if snapshot_root is not None
            else self.db_path.parent
        )
        discovered_ini, discovered_script_location = discover_module_alembic_paths(
            self.module_id
        )
        self.alembic_ini_path = (
            Path(alembic_ini_path).expanduser().resolve(strict=False)
            if alembic_ini_path is not None
            else discovered_ini
        )
        self.alembic_script_location = (
            Path(alembic_script_location).expanduser().resolve(strict=False)
            if alembic_script_location is not None
            else discovered_script_location
        )
        self.default_backup_mode = default_backup_mode
        self.sqlite3_bin = sqlite3_bin
        self.target_user_version = target_user_version
        self.verifier_hook = verifier_hook
        self.backend_type = str(backend_type or "sqlite").strip().lower()
        self.engine = engine
        self._telemetry_hook = (
            telemetry_hook if telemetry_hook is not None else NoopStorageTelemetryHook()
        )

    @dataclass
    class _OperationTelemetry:
        token: object | None
        started_at: float
        operation: str
        success: bool = False
        error: str | None = None

    @contextmanager
    def _instrument_operation(self, operation: str):
        state = self._OperationTelemetry(
            token=self._telemetry_hook.on_migration_start(self.module_id, operation),
            started_at=time.perf_counter(),
            operation=operation,
        )
        try:
            yield state
        except Exception as exc:  # noqa: BLE001
            state.error = str(exc)
            raise
        finally:
            duration_ms = (time.perf_counter() - state.started_at) * 1000.0
            self._telemetry_hook.on_migration_end(
                state.token,
                self.module_id,
                state.operation,
                duration_ms,
                state.success,
                state.error,
            )

    def detect(self) -> DbState:
        if self.backend_type == "postgres":
            return self._detect_postgres()
        if not self.db_path.exists():
            return DbState(
                module_id=self.module_id,
                db_path=str(self.db_path),
                exists=False,
                application_id=None,
                expected_application_id=self.module_application_id,
                application_id_matches=False,
                user_version=0,
                alembic_revision=None,
                om_meta={},
            )

        with sqlite3.connect(str(self.db_path)) as conn:
            application_id = int(self._pragma_int(conn, "application_id", default=0))
            user_version = int(self._pragma_int(conn, "user_version", default=0))
            om_meta = self._read_om_meta(conn)
            revision = self._read_alembic_revision(conn)

        return DbState(
            module_id=self.module_id,
            db_path=str(self.db_path),
            exists=True,
            application_id=application_id,
            expected_application_id=self.module_application_id,
            application_id_matches=application_id == self.module_application_id,
            user_version=user_version,
            alembic_revision=revision,
            om_meta=om_meta,
        )

    def backup(self, *, mode: str | None = None):
        with self._instrument_operation("backup") as telemetry:
            if self.backend_type == "postgres":
                raise DbIdentityError(
                    "snapshot backup is only supported for sqlite backends"
                )

            state = self.detect()
            if not state.exists:
                raise DbIdentityError(f"Database file does not exist: {self.db_path}")
            if (
                state.application_id is not None
                and state.application_id != 0
                and state.application_id != self.module_application_id
            ):
                raise DbIdentityError(
                    f"application_id mismatch for module '{self.module_id}': expected {self.module_application_id}, "
                    f"found {state.application_id}"
                )

            schema_head = state.om_meta.get("schema_head") or state.alembic_revision

            artifact = create_snapshot(
                module_id=self.module_id,
                source_db_path=self.db_path,
                snapshot_root=self.snapshot_root,
                mode=mode or self.default_backup_mode,
                user_version=state.user_version,
                schema_head=schema_head,
                sqlite3_bin=self.sqlite3_bin,
            )
            telemetry.success = True
            return artifact

    def restore(
        self, *, snapshot_path: str | Path, target_db_path: str | Path | None = None
    ) -> None:
        with self._instrument_operation("restore") as telemetry:
            target = (
                Path(target_db_path or self.db_path).expanduser().resolve(strict=False)
            )
            restore_snapshot(snapshot_path=Path(snapshot_path), target_db_path=target)
            telemetry.success = True

    def migrate(self, *, target: str = "head") -> MigrationReport:
        return self._migrate_impl(
            operation="migrate",
            target=target,
            auto_rollback=True,
        )

    def migrate_with_verify(
        self,
        *,
        target: str = "head",
        auto_rollback: bool = True,
    ) -> MigrationReport:
        return self._migrate_impl(
            operation="migrate_with_verify",
            target=target,
            auto_rollback=auto_rollback,
        )

    def _migrate_impl(
        self,
        *,
        operation: str,
        target: str,
        auto_rollback: bool,
    ) -> MigrationReport:
        with self._instrument_operation(operation) as telemetry:
            if self.backend_type == "postgres":
                report = self._migrate_postgres(
                    target=target,
                    auto_rollback=auto_rollback,
                )
                telemetry.success = report.success
                telemetry.error = report.error
                return report

            started = time.monotonic()
            before = self.detect()

            if not before.exists:
                raise DbIdentityError(f"Cannot migrate missing DB: {self.db_path}")
            if (
                before.application_id is not None
                and before.application_id != 0
                and before.application_id != self.module_application_id
            ):
                raise DbIdentityError(
                    f"application_id mismatch for module '{self.module_id}': expected {self.module_application_id}, "
                    f"found {before.application_id}"
                )

            backup_artifact = self.backup()

            verification = self.verify(level="quick")
            success = False
            rolled_back = False
            error: str | None = None
            try:
                self._apply_alembic_upgrade(target=target)
                self._update_metadata_after_migration(target=target)
                verification = self.verify(level="quick")
                if not verification.ok:
                    raise VerificationError(
                        "Post-migration verification failed with fatal findings."
                    )
                success = True
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                if auto_rollback:
                    restore_snapshot(
                        snapshot_path=Path(backup_artifact.snapshot_path),
                        target_db_path=self.db_path,
                    )
                    rolled_back = True
                verification = self.verify(level="quick")

            after = self.detect()
            duration_ms = int((time.monotonic() - started) * 1000)
            report = MigrationReport(
                module_id=self.module_id,
                db_path=str(self.db_path),
                target=target,
                before=before,
                after=after,
                backup=backup_artifact,
                verification=verification,
                success=success,
                duration_ms=duration_ms,
                rolled_back=rolled_back,
                error=error,
            )
            telemetry.success = report.success
            telemetry.error = report.error
            return report

    def verify(self, *, level: str = "quick"):
        with self._instrument_operation("verify") as telemetry:
            report = run_verification(
                module_id=self.module_id,
                db_path=self.db_path,
                level=level,
                verifier_hook=self.verifier_hook,
                raise_on_fatal=False,
                engine=self.engine if self.backend_type == "postgres" else None,
            )
            telemetry.success = bool(report.ok)
            if not report.ok:
                telemetry.error = "verification returned fatal findings"
            return report

    def fallback_rehydrate(
        self,
        *,
        source_db_path: str | Path,
        target_db_path: str | Path,
        omx_dir: str | Path,
    ) -> RehydrateReport:
        with self._instrument_operation("fallback_rehydrate") as telemetry:
            from openminion.modules.storage.migrations.transfer import import_omx

            report = import_omx(
                omx_dir=omx_dir,
                target_db_path=target_db_path,
            )
            telemetry.success = bool(report.success)
            telemetry.error = report.error
            if not report.success:
                raise RehydrateError(report.error or "OMX rehydrate failed")
            return report

    def _apply_alembic_upgrade(
        self,
        *,
        target: str,
        connection: sqlite3.Connection | SAConnection | None = None,
    ) -> None:
        if self.alembic_script_location is None and self.alembic_ini_path is None:
            raise MigrationApplyError(
                "Alembic configuration missing. Provide alembic_ini_path or alembic_script_location."
            )

        try:
            from alembic import command
            from alembic.config import Config
        except Exception as exc:  # noqa: BLE001
            import importlib.util
            import sys

            alembic_spec = importlib.util.find_spec("alembic")
            raise MigrationApplyError(
                "Alembic import failed during migration execution: "
                f"{exc}; executable={sys.executable}; "
                f"alembic_spec={getattr(alembic_spec, 'origin', None)}"
            ) from exc

        cfg = Config(str(self.alembic_ini_path)) if self.alembic_ini_path else Config()
        if self.alembic_script_location is not None:
            cfg.set_main_option("script_location", str(self.alembic_script_location))
        if self.backend_type == "postgres":
            if connection is not None:
                cfg.attributes["connection"] = connection
            elif self.engine is not None:
                cfg.attributes["engine"] = self.engine
                cfg.set_main_option(
                    "sqlalchemy.url",
                    self.engine.url.render_as_string(hide_password=False),
                )
            else:
                raise MigrationApplyError(
                    "Postgres migration requires a SQLAlchemy engine or connection."
                )
        else:
            cfg.set_main_option("sqlalchemy.url", f"sqlite:///{self.db_path}")
            if connection is not None:
                cfg.attributes["connection"] = connection

        try:
            command.upgrade(cfg, target)
        except Exception as exc:  # noqa: BLE001
            raise MigrationApplyError(
                f"Alembic upgrade failed for module '{self.module_id}': {exc}"
            ) from exc

    def _update_metadata_after_migration(
        self,
        *,
        target: str,
        connection: sqlite3.Connection | SAConnection | None = None,
    ) -> None:
        migrated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

        if self.backend_type == "postgres":
            if connection is None:
                raise MigrationApplyError(
                    "Postgres metadata update requires an active SQLAlchemy connection."
                )
            self._update_postgres_metadata_after_migration(
                target=target,
                migrated_at=migrated_at,
                connection=connection,
            )
            return

        self._update_sqlite_metadata_after_migration(
            target=target,
            migrated_at=migrated_at,
        )

    def _update_postgres_metadata_after_migration(
        self,
        *,
        target: str,
        migrated_at: str,
        connection: SAConnection,
    ) -> None:
        from sqlalchemy import text

        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS om_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
        )
        current_revision = self._ensure_postgres_alembic_revision(
            target=target,
            connection=connection,
        )
        self._upsert_postgres_om_meta(
            connection=connection,
            values={
                "module_id": self.module_id,
                "schema_head": str(current_revision or target),
                "last_migrated_at": migrated_at,
            },
        )

    def _ensure_postgres_alembic_revision(
        self,
        *,
        target: str,
        connection: SAConnection,
    ) -> str | None:
        current_revision = self._read_alembic_revision_pg(connection)
        if current_revision is not None:
            return current_revision
        current_revision = self._resolve_target_revision(target)
        if current_revision is None:
            return None
        from sqlalchemy import text

        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS alembic_version (
                    version_num TEXT PRIMARY KEY
                )
                """
            )
        )
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(
            text("INSERT INTO alembic_version(version_num) VALUES (:revision)"),
            {"revision": current_revision},
        )
        return current_revision

    def _upsert_postgres_om_meta(
        self,
        *,
        connection: SAConnection,
        values: dict[str, str],
    ) -> None:
        from sqlalchemy import text

        for key, value in values.items():
            connection.execute(
                text(
                    """
                    INSERT INTO om_meta(key, value)
                    VALUES (:key, :value)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """
                ),
                {"key": key, "value": value},
            )

    def _update_sqlite_metadata_after_migration(
        self,
        *,
        target: str,
        migrated_at: str,
    ) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(f"PRAGMA application_id={self.module_application_id}")
            if self.target_user_version is not None:
                conn.execute(f"PRAGMA user_version={int(self.target_user_version)}")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS om_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            current_revision = self._ensure_sqlite_alembic_revision(
                target=target,
                connection=conn,
            )
            self._upsert_sqlite_om_meta(
                connection=conn,
                values={
                    "module_id": self.module_id,
                    "schema_head": str(current_revision or target),
                    "last_migrated_at": migrated_at,
                },
            )
            conn.commit()

    def _ensure_sqlite_alembic_revision(
        self,
        *,
        target: str,
        connection: sqlite3.Connection,
    ) -> str | None:
        current_revision = self._read_alembic_revision(connection)
        if current_revision is not None:
            return current_revision
        current_revision = self._resolve_target_revision(target)
        if current_revision is None:
            return None
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num TEXT PRIMARY KEY
            )
            """
        )
        connection.execute("DELETE FROM alembic_version")
        connection.execute(
            "INSERT INTO alembic_version(version_num) VALUES (?)",
            (current_revision,),
        )
        return current_revision

    def _upsert_sqlite_om_meta(
        self,
        *,
        connection: sqlite3.Connection,
        values: dict[str, str],
    ) -> None:
        for key, value in values.items():
            connection.execute(
                """
                INSERT INTO om_meta(key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    @classmethod
    def _read_om_meta(cls, conn: sqlite3.Connection) -> dict[str, str]:
        if not cls._table_exists(conn, "om_meta"):
            return {}

        rows = conn.execute("SELECT key, value FROM om_meta").fetchall()
        payload: dict[str, str] = {}
        for key, value in rows:
            payload[str(key)] = "" if value is None else str(value)
        return payload

    @classmethod
    def _read_alembic_revision(cls, conn: sqlite3.Connection) -> str | None:
        if not cls._table_exists(conn, "alembic_version"):
            return None

        row = conn.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
        if row is None or row[0] is None:
            return None
        return str(row[0])

    @staticmethod
    def _pragma_int(
        conn: sqlite3.Connection, pragma_name: str, *, default: int = 0
    ) -> int:
        row = conn.execute(f"PRAGMA {pragma_name}").fetchone()
        if row is None or row[0] is None:
            return int(default)
        return int(row[0])

    def _resolve_target_revision(self, target: str) -> str | None:
        normalized = str(target or "").strip()
        if normalized and normalized != "head":
            return normalized
        if self.alembic_script_location is None:
            return normalized or None
        try:
            from alembic.config import Config
            from alembic.script import ScriptDirectory
        except Exception:  # noqa: BLE001
            return normalized or None

        cfg = Config(str(self.alembic_ini_path)) if self.alembic_ini_path else Config()
        cfg.set_main_option("script_location", str(self.alembic_script_location))
        script = ScriptDirectory.from_config(cfg)
        heads = script.get_heads()
        return str(heads[0]) if heads else (normalized or None)

    def _require_engine(self) -> Engine:
        if self.engine is None:
            raise MigrationApplyError(
                "backend_type='postgres' requires a SQLAlchemy engine"
            )
        return self.engine

    def _detect_postgres(self) -> DbState:
        engine = self._require_engine()
        with engine.connect() as connection:
            revision = self._read_alembic_revision_pg(connection)
            om_meta = self._read_om_meta_pg(connection)
        return DbState(
            module_id=self.module_id,
            db_path=str(self.db_path),
            exists=True,
            application_id=None,
            expected_application_id=self.module_application_id,
            application_id_matches=True,
            user_version=0,
            alembic_revision=revision,
            om_meta=om_meta,
        )

    def _migrate_postgres(
        self,
        *,
        target: str,
        auto_rollback: bool,
    ) -> MigrationReport:
        started = time.monotonic()
        before = self.detect()
        engine = self._require_engine()
        backup_artifact = BackupArtifact(
            module_id=self.module_id,
            source_db_path=str(self.db_path),
            snapshot_path="",
            mode="transactional_ddl",
            created_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            user_version=before.user_version,
            schema_head=before.om_meta.get("schema_head") or before.alembic_revision,
        )

        success = False
        rolled_back = False
        error: str | None = None
        verification = self.verify(level="quick")
        try:
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    self._apply_alembic_upgrade(target=target, connection=connection)
                    self._update_metadata_after_migration(
                        target=target,
                        connection=connection,
                    )
                    verification = run_verification(
                        module_id=self.module_id,
                        db_path=self.db_path,
                        level="quick",
                        verifier_hook=self.verifier_hook,
                        raise_on_fatal=False,
                        connection=connection,
                    )
                    if not verification.ok:
                        if auto_rollback and transaction.is_active:
                            transaction.rollback()
                            rolled_back = True
                        elif transaction.is_active:
                            transaction.commit()
                        raise VerificationError(
                            "Post-migration verification failed with fatal findings."
                        )
                    transaction.commit()
                    success = True
                except Exception:
                    if transaction.is_active:
                        transaction.rollback()
                        rolled_back = True
                    raise
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            verification = self.verify(level="quick")

        after = self.detect()
        duration_ms = int((time.monotonic() - started) * 1000)
        return MigrationReport(
            module_id=self.module_id,
            db_path=str(self.db_path),
            target=target,
            before=before,
            after=after,
            backup=backup_artifact,
            verification=verification,
            success=success,
            duration_ms=duration_ms,
            rolled_back=rolled_back,
            error=error,
        )

    @staticmethod
    def _read_om_meta_pg(conn: SAConnection) -> dict[str, str]:
        from sqlalchemy import inspect, text

        inspector = inspect(conn)
        if not inspector.has_table("om_meta"):
            return {}
        rows = conn.execute(text("SELECT key, value FROM om_meta")).mappings().all()
        return {
            str(row["key"]): "" if row["value"] is None else str(row["value"])
            for row in rows
        }

    @staticmethod
    def _read_alembic_revision_pg(conn: SAConnection) -> str | None:
        from sqlalchemy import inspect, text

        inspector = inspect(conn)
        if not inspector.has_table("alembic_version"):
            return None
        row = conn.execute(
            text("SELECT version_num FROM alembic_version LIMIT 1")
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return str(row[0])
