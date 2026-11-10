import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

_NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

_PRIVATE_DIRECTORY_MODE = 0o700


def ensure_private_directory(path: str | Path) -> Path:
    """Create the missing components of ``path`` with mode 0700.

    SQLite writes its database and sidecars with the process umask, so a 0644 file is
    unreachable to other users only while its directory is private. The mode is applied
    at creation and nowhere else: an existing component may be a caller-owned root that
    keeps its own mode, and a creator that wins the race keeps its mode too.
    """
    target = Path(path).expanduser()
    missing: list[Path] = []
    for candidate in (target, *target.parents):
        if candidate.exists() or candidate.is_symlink():
            break
        missing.append(candidate)
    if not missing:
        if not target.is_dir():
            raise NotADirectoryError(str(target))
        return target
    for created in reversed(missing):
        try:
            created.mkdir(mode=_PRIVATE_DIRECTORY_MODE)
        except FileExistsError:
            # Someone else created it between the walk and this call: their mode
            # stands, and a non-directory here is a real failure, not a race to skip.
            if not created.is_dir():
                raise NotADirectoryError(str(created)) from None
    return target


def append_jsonl(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path).expanduser().resolve(strict=False)
    ensure_private_directory(target.parent)
    line = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    )
    with target.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    _fsync_directory(target.parent)


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    target = Path(path).expanduser().resolve(strict=False)
    ensure_private_directory(target.parent)
    tmp = target.parent / f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    with tmp.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)
    _fsync_directory(target.parent)


def atomic_write_text(path: str | Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def normalize_namespace(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if not _NAMESPACE_RE.match(text):
        raise ValueError(f"invalid namespace: {value}")
    return text


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
