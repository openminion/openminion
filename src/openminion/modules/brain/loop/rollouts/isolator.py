"""Tempdir isolator for parallel rollouts."""

import shutil
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


@dataclass
class WorktreeIsolator:
    """Allocates temp dirs and cleans them up."""

    run_id: str = ""
    parent_root: Path | None = None
    _created: list[Path] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = uuid.uuid4().hex[:12]

    def allocate(self, n: int) -> list[Path]:
        """Create rollout temp dirs."""

        dirs: list[Path] = []
        for _ in range(int(n)):
            d = Path(tempfile.mkdtemp(prefix=f"rollout-{self.run_id}-"))
            dirs.append(d)
            self._created.append(d)
        return dirs

    def release(self) -> None:
        """Remove all worktrees created via this isolator (idempotent)."""

        leftover: list[Path] = []
        created = list(self._created)
        self._created.clear()
        for d in created:
            try:
                shutil.rmtree(d, ignore_errors=False)
            except OSError:
                leftover.append(d)
        if leftover:
            for d in leftover:
                shutil.rmtree(d, ignore_errors=True)

    def assert_no_leaks(self) -> None:
        """Raise if any allocated worktree still exists on disk."""

        present = [d for d in self._created if d.exists()]
        if present:
            raise RuntimeError(
                f"WorktreeIsolator leak: {len(present)} worktrees still exist"
            )

    @contextmanager
    def worktrees(self, n: int) -> Iterator[list[Path]]:
        dirs = self.allocate(n)
        try:
            yield dirs
        finally:
            self.release()
