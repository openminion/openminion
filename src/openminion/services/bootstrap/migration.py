import logging
import re
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, List

from openminion.services.bootstrap.paths import (
    SERVICES_IDENTITY_SUBDIR,
    SERVICES_MEMORY_SUBDIR,
    SERVICES_SESSION_CONTEXT_SUBDIR,
    SERVICES_STATE_DIRNAME,
    SERVICES_TOOL_RUNTIME_SUBDIR,
)

from openminion.base.time import utc_now_iso as _utc_now_iso

LEGACY_DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class MigrationItem:
    source: str
    target: str
    status: str
    details: str = ""


@dataclass(frozen=True)
class MigrationReport:
    started_at: str
    finished_at: str
    dry_run: bool
    items: List[MigrationItem]

    def to_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "dry_run": self.dry_run,
            "items": [asdict(item) for item in self.items],
        }


def migrate_data_root(
    *,
    home_root: Path,
    data_root: Path,
    dry_run: bool = False,
    logger: logging.Logger | None = None,
) -> MigrationReport:
    log = logger or logging.getLogger("openminion.data_migration")
    items: list[MigrationItem] = []
    started_at = _utc_now_iso()

    home_root = home_root.expanduser().resolve(strict=False)
    data_root = data_root.expanduser().resolve(strict=False)

    if not dry_run:
        data_root.mkdir(parents=True, exist_ok=True)

    nested_openminion = data_root / ".openminion"
    if nested_openminion.exists() and nested_openminion.is_dir():
        _migrate_openminion_dir(
            source=nested_openminion,
            data_root=data_root,
            items=items,
            dry_run=dry_run,
            logger=log,
        )
    nested_openminion_data = data_root / ".openminion-data"
    if nested_openminion_data.exists() and nested_openminion_data.is_dir():
        _migrate_openminion_dir(
            source=nested_openminion_data,
            data_root=data_root,
            items=items,
            dry_run=dry_run,
            logger=log,
        )

    for source in _iter_legacy_paths(home_root, data_root=data_root):
        if source.name in {".openminion", ".openminion-data"}:
            _migrate_openminion_dir(
                source=source,
                data_root=data_root,
                items=items,
                dry_run=dry_run,
                logger=log,
            )
            continue

        if source.name == ".tmp":
            target = data_root / "runtime"
            _merge_or_move(source, target, items=items, dry_run=dry_run, logger=log)
            continue

        if source.name == "storage":
            target = data_root / "storage"
            _merge_or_move(source, target, items=items, dry_run=dry_run, logger=log)
            continue

        if LEGACY_DATE_DIR.match(source.name):
            target = data_root / "tool-runs" / source.name
            _merge_or_move(source, target, items=items, dry_run=dry_run, logger=log)
            continue

    finished_at = _utc_now_iso()
    return MigrationReport(
        started_at=started_at,
        finished_at=finished_at,
        dry_run=dry_run,
        items=items,
    )


def _iter_legacy_paths(
    home_root: Path, *, data_root: Path | None = None
) -> Iterable[Path]:
    if not home_root.exists():
        return
    normalized_data_root = (
        data_root.expanduser().resolve(strict=False) if data_root else None
    )
    for entry in home_root.iterdir():
        if normalized_data_root and entry.resolve(strict=False) == normalized_data_root:
            continue
        if (
            entry.name in {".openminion", ".openminion-data", ".tmp", "storage"}
            and entry.exists()
        ):
            yield entry
        elif entry.is_dir() and LEGACY_DATE_DIR.match(entry.name):
            yield entry


def _merge_or_move(
    source: Path,
    target: Path,
    *,
    items: list[MigrationItem],
    dry_run: bool,
    logger: logging.Logger,
) -> None:
    if not source.exists():
        items.append(
            MigrationItem(
                source=str(source),
                target=str(target),
                status="skipped",
                details="source missing",
            )
        )
        return

    if target.exists() and source.is_dir() and target.is_dir():
        _merge_dir(source, target, items=items, dry_run=dry_run, logger=logger)
        return

    if target.exists():
        items.append(
            MigrationItem(
                source=str(source),
                target=str(target),
                status="skipped",
                details="target exists",
            )
        )
        return

    items.append(
        MigrationItem(
            source=str(source),
            target=str(target),
            status="moved" if not dry_run else "planned",
        )
    )
    if dry_run:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    logger.info("moved legacy path %s -> %s", source, target)


def _merge_dir(
    source: Path,
    target: Path,
    *,
    items: list[MigrationItem],
    dry_run: bool,
    logger: logging.Logger,
) -> None:
    target.mkdir(parents=True, exist_ok=True)
    moved_any = False
    for child in source.iterdir():
        dest = target / child.name
        if dest.exists():
            items.append(
                MigrationItem(
                    source=str(child),
                    target=str(dest),
                    status="skipped",
                    details="target exists",
                )
            )
            continue
        items.append(
            MigrationItem(
                source=str(child),
                target=str(dest),
                status="moved" if not dry_run else "planned",
            )
        )
        moved_any = True
        if dry_run:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(child), str(dest))
    if not dry_run and moved_any:
        try:
            source.rmdir()
        except OSError:
            pass
    logger.info("merged legacy directory %s -> %s", source, target)


def _migrate_openminion_dir(
    *,
    source: Path,
    data_root: Path,
    items: list[MigrationItem],
    dry_run: bool,
    logger: logging.Logger,
) -> None:
    mapping = {
        SERVICES_STATE_DIRNAME: data_root / SERVICES_STATE_DIRNAME,
        SERVICES_IDENTITY_SUBDIR: data_root / SERVICES_IDENTITY_SUBDIR,
        "telemetry": data_root / "telemetry",
        SERVICES_MEMORY_SUBDIR: data_root / SERVICES_MEMORY_SUBDIR,
        SERVICES_SESSION_CONTEXT_SUBDIR: data_root / SERVICES_SESSION_CONTEXT_SUBDIR,
        SERVICES_TOOL_RUNTIME_SUBDIR: data_root / SERVICES_TOOL_RUNTIME_SUBDIR,
        "storage": data_root / "storage",
        "artifact": data_root / "artifact",
        "retrieve": data_root / "retrieve",
        "skill": data_root / "skill",
        "a2a": data_root / "a2a",
        "registry": data_root / "registry",
        "controlplane": data_root / "controlplane",
        "browser": data_root / "browser",
        "browser-playwright": data_root / "browser-playwright",
        "policy": data_root / "policy",
        "session": data_root / "session",
        "telegram": data_root / "controlplane" / "telegram",
        "logs": data_root / "logs",
    }
    legacy_root = data_root / "legacy" / "openminion"

    for child in source.iterdir():
        target = mapping.get(child.name, legacy_root / child.name)
        _merge_or_move(child, target, items=items, dry_run=dry_run, logger=logger)

    if not dry_run:
        try:
            source.rmdir()
        except OSError:
            pass
