from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from .contracts import ChangePlan


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


ChangeVerifier = Callable[[Path], bool]


def _replace(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_local_change(
    plan: ChangePlan,
    *,
    approved: bool,
    allowed_root: Path,
    verify: ChangeVerifier | None = None,
) -> str:
    """Apply one approved local file replacement atomically and return its digest."""
    if not approved:
        raise PermissionError("write-safe changes require explicit approval")
    path = Path(plan.path).expanduser().resolve()
    root = allowed_root.expanduser().resolve()
    if not path.is_relative_to(root):
        raise PermissionError("write-safe change path is outside the allowed root")
    if plan.expected_digest and file_digest(path) != plan.expected_digest:
        raise ValueError("change plan is stale")
    existed = path.exists()
    original = path.read_bytes() if existed else b""
    _replace(path, plan.content.encode())
    try:
        if (
            plan.expected_content is not None
            and path.read_text() != plan.expected_content
        ):
            raise RuntimeError("write-safe postcondition did not match")
        if verify is not None and not verify(path):
            raise RuntimeError("write-safe verification failed")
    except Exception:
        if plan.rollback_on_failure:
            if existed:
                _replace(path, original)
            else:
                path.unlink(missing_ok=True)
        raise
    return file_digest(path)
