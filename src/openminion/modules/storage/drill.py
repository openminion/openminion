import hashlib
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from openminion.modules.storage.migrations.errors import StorageMigrationError
from openminion.modules.storage.migrations.models import (
    BackupArtifact,
    VerificationReport,
)
from openminion.modules.storage.migrations.runner import MigrationRunner


@dataclass(frozen=True)
class DrillReport:
    """Outcome of one storage restore drill."""

    module_id: str
    source_db_path: str
    source_sha256: str | None
    snapshot_path: str
    restore_target_path: str
    restore_duration_seconds: float
    verification: VerificationReport | None
    backup_artifact: BackupArtifact
    ok: bool = False
    error: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["verification"] = (
            self.verification.to_dict() if self.verification is not None else None
        )
        payload["backup_artifact"] = self.backup_artifact.to_dict()
        return payload


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_drill(
    *,
    runner: MigrationRunner,
    target_path: Path | str,
    verify_level: str = "full",
) -> DrillReport:
    """Run backup, restore, and verification against ``runner``'s database."""

    target_path = Path(target_path).expanduser().resolve(strict=False)
    if target_path.exists() and target_path.is_dir():
        raise StorageMigrationError(
            f"drill target_path must be a file path, not a directory: {target_path}"
        )

    source_db_path = Path(runner.db_path)
    source_sha = (
        _sha256_file(source_db_path) if runner.backend_type == "sqlite" else None
    )

    backup_artifact = runner.backup()
    snapshot_path = Path(backup_artifact.snapshot_path)

    restore_started = time.monotonic()
    restore_error: str | None = None
    try:
        runner.restore(snapshot_path=snapshot_path, target_db_path=target_path)
    except Exception as exc:  # noqa: BLE001
        restore_error = str(exc)
    restore_duration = max(0.0, time.monotonic() - restore_started)

    verification: VerificationReport | None = None
    if restore_error is None:
        sibling = MigrationRunner(
            module_id=runner.module_id,
            db_path=target_path,
            module_application_id=runner.module_application_id,
            snapshot_root=runner.snapshot_root,
            alembic_ini_path=runner.alembic_ini_path,
            alembic_script_location=runner.alembic_script_location,
            default_backup_mode=runner.default_backup_mode,
            sqlite3_bin=runner.sqlite3_bin,
            target_user_version=runner.target_user_version,
            verifier_hook=runner.verifier_hook,
            backend_type=runner.backend_type,
            engine=runner.engine,
        )
        try:
            verification = sibling.verify(level=verify_level)
        except Exception as exc:  # noqa: BLE001
            restore_error = f"verify failed: {exc}"

    ok = restore_error is None and verification is not None and bool(verification.ok)

    return DrillReport(
        module_id=runner.module_id,
        source_db_path=str(source_db_path),
        source_sha256=source_sha,
        snapshot_path=str(snapshot_path),
        restore_target_path=str(target_path),
        restore_duration_seconds=round(restore_duration, 6),
        verification=verification,
        backup_artifact=backup_artifact,
        ok=ok,
        error=restore_error,
    )


__all__ = ["DrillReport", "run_drill"]
