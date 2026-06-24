"""Shared helpers for line-oriented validator baselines."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


def load_baseline_lines(
    path: Path,
    *,
    allow_comments: bool = True,
) -> set[str]:
    """Load non-empty baseline lines from disk."""
    if not path.is_file():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    return {
        line.strip()
        for line in lines
        if line.strip() and (not allow_comments or not line.startswith("#"))
    }


def write_baseline_lines(
    path: Path,
    lines: Iterable[str],
    *,
    header: str | None = None,
) -> None:
    """Write a baseline file with an optional comment header."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(sorted(lines))
    path.write_text(
        (header or "") + body + ("\n" if body else ""),
        encoding="utf-8",
    )
