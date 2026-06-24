from __future__ import annotations

from pathlib import Path
from typing import Callable

from openminion.modules.storage.interfaces import (
    STORAGE_INTERFACE_VERSION,
    StorageEnvelope,
    StorageError,
)
from openminion.modules.storage.migrations.models import (
    BackupArtifact,
    DbState,
    MigrationReport,
    RehydrateReport,
    VerificationReport,
)
from openminion.modules.storage.migrations.runner import MigrationRunner, VerifierHook


class StorageModuleOps:
    """Concrete implementation of ModuleStorageOpsInterface."""

    contract_version: str = STORAGE_INTERFACE_VERSION

    def __init__(
        self,
        *,
        module_id: str,
        db_path: str | Path,
        module_application_id: int,
        snapshot_root: str | Path | None = None,
        migrations_fn: Callable[[], list[str]] | None = None,
        verifier_hook: VerifierHook | None = None,
        alembic_ini_path: str | Path | None = None,
        alembic_script_location: str | Path | None = None,
        target_user_version: int | None = None,
    ) -> None:
        self.module_id = str(module_id).strip()
        self.module_application_id = int(module_application_id)
        self._db_path = Path(db_path).expanduser().resolve(strict=False)
        self._migrations_fn = migrations_fn
        self._runner = MigrationRunner(
            module_id=self.module_id,
            db_path=self._db_path,
            module_application_id=self.module_application_id,
            snapshot_root=snapshot_root,
            verifier_hook=verifier_hook,
            alembic_ini_path=alembic_ini_path,
            alembic_script_location=alembic_script_location,
            target_user_version=target_user_version,
        )

    # -- detect ---------------------------------------------------------------

    def detect(self) -> DbState:
        return self._runner.detect()

    def detect_envelope(self) -> StorageEnvelope:
        try:
            state = self.detect()
            return StorageEnvelope(
                operation="detect",
                ok=True,
                data=state.to_dict(),
                module=self.module_id,
            )
        except Exception as exc:
            return self._error_envelope("detect", exc)

    # -- verify ---------------------------------------------------------------

    def verify(self, *, level: str = "quick") -> VerificationReport:
        return self._runner.verify(level=level)

    def verify_envelope(self, *, level: str = "quick") -> StorageEnvelope:
        try:
            report = self.verify(level=level)
            return StorageEnvelope(
                operation="verify",
                ok=report.ok,
                data=report.to_dict(),
                module=self.module_id,
            )
        except Exception as exc:
            return self._error_envelope("verify", exc)

    # -- backup ---------------------------------------------------------------

    def backup(self, *, mode: str | None = None) -> BackupArtifact:
        return self._runner.backup(mode=mode)

    def backup_envelope(self, *, mode: str | None = None) -> StorageEnvelope:
        try:
            artifact = self.backup(mode=mode)
            return StorageEnvelope(
                operation="backup",
                ok=True,
                data=artifact.to_dict(),
                module=self.module_id,
            )
        except Exception as exc:
            return self._error_envelope("backup", exc)

    # -- restore --------------------------------------------------------------

    def restore(
        self,
        *,
        snapshot_path: str | Path,
        target_db_path: str | Path | None = None,
    ) -> DbState:
        self._runner.restore(
            snapshot_path=snapshot_path,
            target_db_path=target_db_path,
        )
        verification = self._runner.verify(level="quick")
        if not verification.ok:
            raise RuntimeError("Post-restore verification failed")
        return self._runner.detect()

    def restore_envelope(
        self,
        *,
        snapshot_path: str | Path,
        target_db_path: str | Path | None = None,
    ) -> StorageEnvelope:
        try:
            state = self.restore(
                snapshot_path=snapshot_path,
                target_db_path=target_db_path,
            )
            return StorageEnvelope(
                operation="restore",
                ok=True,
                data=state.to_dict(),
                module=self.module_id,
            )
        except Exception as exc:
            return self._error_envelope("restore", exc)

    # -- migrate --------------------------------------------------------------

    def migrate(self, *, target: str = "head") -> MigrationReport:
        return self._runner.migrate(target=target)

    def migrate_envelope(self, *, target: str = "head") -> StorageEnvelope:
        try:
            report = self.migrate(target=target)
            return StorageEnvelope(
                operation="migrate",
                ok=report.success,
                data=report.to_dict(),
                module=self.module_id,
            )
        except Exception as exc:
            return self._error_envelope("migrate", exc)

    # -- export ---------------------------------------------------------------

    def export(self, *, export_dir: str | Path) -> StorageEnvelope:
        try:
            from openminion.modules.storage.migrations.transfer import export_omx

            manifest = export_omx(
                db_path=self._db_path,
                module_id=self.module_id,
                module_application_id=self.module_application_id,
                export_dir=export_dir,
            )
            return StorageEnvelope(
                operation="export",
                ok=True,
                data=manifest.to_dict(),
                module=self.module_id,
            )
        except Exception as exc:
            return self._error_envelope("export", exc)

    # -- rehydrate (import) ---------------------------------------------------

    def rehydrate(
        self,
        *,
        source_db_path: str | Path,
        target_db_path: str | Path,
        omx_dir: str | Path,
    ) -> RehydrateReport:
        return self._runner.fallback_rehydrate(
            source_db_path=source_db_path,
            target_db_path=target_db_path,
            omx_dir=omx_dir,
        )

    def rehydrate_envelope(
        self,
        *,
        source_db_path: str | Path,
        target_db_path: str | Path,
        omx_dir: str | Path,
    ) -> StorageEnvelope:
        try:
            report = self.rehydrate(
                source_db_path=source_db_path,
                target_db_path=target_db_path,
                omx_dir=omx_dir,
            )
            return StorageEnvelope(
                operation="rehydrate",
                ok=report.success,
                data=report.to_dict(),
                module=self.module_id,
            )
        except Exception as exc:
            return self._error_envelope("rehydrate", exc)

    # -- helpers --------------------------------------------------------------

    def _error_envelope(self, operation: str, exc: Exception) -> StorageEnvelope:
        return StorageEnvelope(
            operation=operation,
            ok=False,
            error=StorageError(
                code=type(exc).__name__,
                message=str(exc),
            ),
            module=self.module_id,
        )


def build_module_ops(
    *,
    module_id: str,
    db_path: str | Path,
    snapshot_root: str | Path | None = None,
    migrations_fn: Callable[[], list[str]] | None = None,
    verifier_hook: VerifierHook | None = None,
) -> StorageModuleOps:
    """Factory: build a StorageModuleOps for a given module_id.

    Resolves module_application_id from the canonical registry.
    """
    from openminion.modules.storage.migrations.module_ids import (
        get_module_application_id,
    )

    return StorageModuleOps(
        module_id=module_id,
        db_path=db_path,
        module_application_id=get_module_application_id(module_id),
        snapshot_root=snapshot_root,
        migrations_fn=migrations_fn,
        verifier_hook=verifier_hook,
    )
