from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


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
        checkpoint_id=checkpoint_id.strip(),
        files=dict(files),
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def restore_file_checkpoint(
    checkpoint: FileCheckpoint, *, root: str | Path = ""
) -> FileRestoreResult:
    """Restore checkpoint files under root and report the outcome."""

    restored: list[str] = []
    missing: list[str] = []
    root_path = Path(root or ".").resolve()
    for relpath, content in checkpoint.files.items():
        path = (root_path / relpath).resolve()
        if not path.is_relative_to(root_path):
            missing.append(relpath)
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
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
