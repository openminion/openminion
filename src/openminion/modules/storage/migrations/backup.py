import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from openminion.modules.storage.migrations.errors import BackupError
from openminion.modules.storage.migrations.models import BackupArtifact


BACKUP_MODE_ONLINE = "online"
BACKUP_MODE_VACUUM_INTO = "vacuum-into"
BACKUP_MODE_CLI = "cli-backup"
SUPPORTED_BACKUP_MODES = {
    BACKUP_MODE_ONLINE,
    BACKUP_MODE_VACUUM_INTO,
    BACKUP_MODE_CLI,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_snapshot_path(
    *,
    source_db_path: Path,
    snapshot_root: Path,
    user_version: int,
    schema_head: str | None,
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    head = schema_head or "none"
    backup_dir = snapshot_root / f"{source_db_path.name}.bak"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir / f"{timestamp}-{user_version}-{head}.sqlite"


def create_snapshot(
    *,
    module_id: str,
    source_db_path: Path,
    snapshot_root: Path,
    mode: str,
    user_version: int,
    schema_head: str | None,
    sqlite3_bin: str = "sqlite3",
) -> BackupArtifact:
    normalized = str(mode).strip().lower()
    if normalized not in SUPPORTED_BACKUP_MODES:
        raise BackupError(
            f"Unsupported backup mode '{mode}'. Expected one of {sorted(SUPPORTED_BACKUP_MODES)}"
        )

    source_db_path = source_db_path.expanduser().resolve(strict=False)
    snapshot_root = snapshot_root.expanduser().resolve(strict=False)

    if not source_db_path.exists():
        raise BackupError(f"Source database does not exist: {source_db_path}")

    snapshot_path = build_snapshot_path(
        source_db_path=source_db_path,
        snapshot_root=snapshot_root,
        user_version=user_version,
        schema_head=schema_head,
    )
    tmp_path = snapshot_path.with_suffix(snapshot_path.suffix + ".tmp")

    if tmp_path.exists():
        tmp_path.unlink()

    if normalized == BACKUP_MODE_ONLINE:
        backup_online(source_db_path, tmp_path)
    elif normalized == BACKUP_MODE_VACUUM_INTO:
        backup_vacuum_into(source_db_path, tmp_path)
    else:
        backup_cli(source_db_path, tmp_path, sqlite3_bin=sqlite3_bin)

    try:
        tmp_path.replace(snapshot_path)
    except Exception as exc:  # noqa: BLE001
        raise BackupError(f"Failed to publish snapshot {snapshot_path}: {exc}") from exc

    return BackupArtifact(
        module_id=module_id,
        source_db_path=str(source_db_path),
        snapshot_path=str(snapshot_path),
        mode=normalized,
        created_at=utc_now_iso(),
        user_version=int(user_version),
        schema_head=schema_head,
    )


def restore_snapshot(*, snapshot_path: Path, target_db_path: Path) -> None:
    snapshot_path = snapshot_path.expanduser().resolve(strict=False)
    target_db_path = target_db_path.expanduser().resolve(strict=False)

    if not snapshot_path.exists():
        raise BackupError(f"Snapshot not found: {snapshot_path}")

    tmp_target = target_db_path.with_suffix(target_db_path.suffix + ".restore.tmp")
    target_db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(snapshot_path, tmp_target)
    tmp_target.replace(target_db_path)


def backup_online(source_db_path: Path, destination_path: Path) -> None:
    source_db_path = source_db_path.expanduser().resolve(strict=False)
    destination_path = destination_path.expanduser().resolve(strict=False)
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with (
            sqlite3.connect(str(source_db_path)) as source_conn,
            sqlite3.connect(str(destination_path)) as dest_conn,
        ):
            source_conn.backup(dest_conn)
            dest_conn.commit()
    except Exception as exc:  # noqa: BLE001
        raise BackupError(
            f"SQLite online backup failed for {source_db_path}: {exc}"
        ) from exc


def backup_vacuum_into(source_db_path: Path, destination_path: Path) -> None:
    source_db_path = source_db_path.expanduser().resolve(strict=False)
    destination_path = destination_path.expanduser().resolve(strict=False)
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    escaped_target = str(destination_path).replace("'", "''")
    sql = f"VACUUM INTO '{escaped_target}'"

    try:
        with sqlite3.connect(str(source_db_path)) as source_conn:
            source_conn.execute(sql)
            source_conn.commit()
    except Exception as exc:  # noqa: BLE001
        raise BackupError(
            f"VACUUM INTO snapshot failed for {source_db_path}: {exc}"
        ) from exc


def backup_cli(
    source_db_path: Path, destination_path: Path, *, sqlite3_bin: str = "sqlite3"
) -> None:
    source_db_path = source_db_path.expanduser().resolve(strict=False)
    destination_path = destination_path.expanduser().resolve(strict=False)
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    command = [sqlite3_bin, str(source_db_path), f".backup {destination_path}"]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise BackupError(
            f"sqlite3 CLI not found for cli-backup mode: {sqlite3_bin}"
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or str(exc)
        raise BackupError(
            f"sqlite3 .backup failed for {source_db_path}: {detail}"
        ) from exc
