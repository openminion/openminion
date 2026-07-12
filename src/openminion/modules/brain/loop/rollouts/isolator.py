"""Filesystem isolation for parallel rollouts."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


@dataclass
class TempdirIsolator:
    """Allocate empty temporary directories for non-code rollouts."""

    run_id: str = ""
    _created: list[Path] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = uuid.uuid4().hex[:12]

    def allocate(self, n: int) -> list[Path]:
        dirs = [
            Path(tempfile.mkdtemp(prefix=f"rollout-{self.run_id}-"))
            for _ in range(int(n))
        ]
        self._created.extend(dirs)
        return dirs

    def release(self) -> None:
        leftover: list[Path] = []
        for path in self._created:
            try:
                shutil.rmtree(path)
            except OSError:
                leftover.append(path)
        self._created = leftover

    def assert_no_leaks(self) -> None:
        present = [path for path in self._created if path.exists()]
        if present:
            raise RuntimeError(f"TempdirIsolator leak: {len(present)} dirs still exist")

    @contextmanager
    def worktrees(self, n: int) -> Iterator[list[Path]]:
        dirs = self.allocate(n)
        try:
            yield dirs
        finally:
            self.release()


@dataclass
class WorktreeIsolator:
    """Create detached Git worktrees for code-bearing rollouts."""

    parent_root: Path
    revision: str = "HEAD"
    run_id: str = ""
    _created: list[Path] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = uuid.uuid4().hex[:12]
        result = self._git("rev-parse", "--show-toplevel")
        if result.returncode != 0:
            raise ValueError(
                f"Worktree parent is not a Git repository: {self.parent_root}"
            )
        self.parent_root = Path(result.stdout.strip()).resolve()

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", str(self.parent_root), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Git worktree command failed: {exc}") from exc

    def allocate(self, n: int) -> list[Path]:
        worktrees: list[Path] = []
        for index in range(int(n)):
            path = Path(tempfile.gettempdir()) / (
                f"openminion-worktree-{self.run_id}-{index}-{uuid.uuid4().hex[:8]}"
            )
            result = self._git("worktree", "add", "--detach", str(path), self.revision)
            if result.returncode != 0:
                self.release()
                detail = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(f"Unable to create Git worktree: {detail}")
            worktrees.append(path)
            self._created.append(path)
        return worktrees

    def release(self) -> None:
        leftover: list[Path] = []
        for path in self._created:
            result = self._git("worktree", "remove", "--force", str(path))
            if result.returncode != 0 and path.exists():
                leftover.append(path)
        self._created = leftover
        self._git("worktree", "prune")

    def assert_no_leaks(self) -> None:
        present = [path for path in self._created if path.exists()]
        if present:
            raise RuntimeError(
                f"WorktreeIsolator leak: {len(present)} worktrees still exist"
            )

    @contextmanager
    def worktrees(self, n: int) -> Iterator[list[Path]]:
        worktrees = self.allocate(n)
        try:
            yield worktrees
        finally:
            self.release()


__all__ = ["TempdirIsolator", "WorktreeIsolator"]
