"""Shared helpers for CI-oriented repo scripts."""

from __future__ import annotations

import json
import os
from pathlib import Path


def load_json_list(raw: str | None) -> list[str]:
    """Parse a JSON list argument into non-empty strings."""
    normalized = str(raw or "").strip()
    if not normalized:
        return []
    loaded = json.loads(normalized)
    if not isinstance(loaded, list):
        raise ValueError("Expected a JSON list")
    return [str(item) for item in loaded if str(item).strip()]


def build_ci_runtime_env(repo_root: Path) -> dict[str, str]:
    """Return the default environment for CI script subprocesses."""
    env = os.environ.copy()
    env.setdefault("OPENMINION_HOME", str(repo_root))
    env.setdefault("OPENMINION_DATA_ROOT", str(repo_root / ".openminion"))
    return env
