from pathlib import Path
import sqlite3
from collections.abc import Callable, Sequence

from openminion.modules.storage.migrations.backup import (
    BACKUP_MODE_ONLINE,
    create_snapshot,
    restore_snapshot,
)
from openminion.modules.storage.migrations.module_ids import schema_head_from_migrations


def _resolve(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _user_version(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute("PRAGMA user_version").fetchone()
        if row is None or row[0] is None:
            return 0
        return int(row[0])
    except Exception:  # noqa: BLE001
        return 0


def backup_module_db(
    module_id: str,
    db_path: str | Path,
    backup_dir: str | Path,
    migrations_fn: Callable[[], Sequence[str]],
) -> Path:
    """Create a snapshot backup for db_path inside backup_dir."""
    src = _resolve(db_path)
    backup_root = _resolve(backup_dir)
    schema_head = schema_head_from_migrations(migrations_fn())
    artifact = create_snapshot(
        module_id=module_id,
        source_db_path=src,
        snapshot_root=backup_root,
        mode=BACKUP_MODE_ONLINE,
        user_version=_user_version(src),
        schema_head=schema_head,
    )
    return Path(artifact.snapshot_path)


def restore_module_db(backup_path: str | Path, target_path: str | Path) -> None:
    """Restore a snapshot into target_path."""
    restore_snapshot(
        snapshot_path=_resolve(backup_path),
        target_db_path=_resolve(target_path),
    )
