"""RepoMapBuilder Protocol."""

from pathlib import Path
from typing import Protocol

from openminion.modules.context.repo_map.schemas import RepoMap


class RepoMapBuilder(Protocol):
    """Parses a directory tree into a typed RepoMap."""

    def parse(self, root: Path) -> RepoMap:  # pragma: no cover - Protocol
        ...


__all__ = ["RepoMapBuilder"]
