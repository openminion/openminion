import os
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class FileCheckpoint:
    """Per-path content snapshot for restore operations."""

    checkpoint_id: str
    files: dict[str, str] = field(default_factory=dict)
    created_at: str = ""


@dataclass(frozen=True)
class FileRestoreResult:
    """Typed restore outcome for one checkpoint."""

    checkpoint_id: str
    restored_paths: tuple[str, ...]
    missing_paths: tuple[str, ...] = field(default_factory=tuple)


def build_file_checkpoint(
    *,
    checkpoint_id: str,
    files: dict[str, str],
) -> FileCheckpoint:
    return FileCheckpoint(
        checkpoint_id=str(checkpoint_id or "").strip(),
        files=dict(files or {}),
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def restore_file_checkpoint(
    checkpoint: FileCheckpoint, *, root: str = ""
) -> FileRestoreResult:
    """Restore checkpoint files under root and report the outcome."""

    restored: list[str] = []
    missing: list[str] = []
    for relpath, content in checkpoint.files.items():
        path = os.path.join(root, relpath) if root else relpath
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError:
                missing.append(relpath)
                continue
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            restored.append(relpath)
        except OSError:
            missing.append(relpath)
    return FileRestoreResult(
        checkpoint_id=checkpoint.checkpoint_id,
        restored_paths=tuple(restored),
        missing_paths=tuple(missing),
    )


__all__ = [
    "FileCheckpoint",
    "FileRestoreResult",
    "build_file_checkpoint",
    "restore_file_checkpoint",
]
