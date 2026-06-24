from __future__ import annotations

from pathlib import Path

DEFAULT_CONFIG_FILENAME = "registry.yaml"
DEFAULT_MANIFEST_FILENAME = "agents.yaml"
DEFAULT_SQLITE_FILENAME = "registry.sqlite3"

STATUS_ORDER: dict[str, int] = {
    "healthy": 0,
    "degraded": 1,
    "unknown": 2,
    "offline": 3,
}

DEFAULT_STANDALONE_SQLITE_SUBPATH = Path(".agentregctl") / DEFAULT_SQLITE_FILENAME
DEFAULT_STANDALONE_MANIFEST_SUBPATH = Path(DEFAULT_MANIFEST_FILENAME)
DEFAULT_INTEGRATED_SQLITE_SUBPATH = Path("registry") / DEFAULT_SQLITE_FILENAME
DEFAULT_INTEGRATED_MANIFEST_SUBPATH = Path("registry") / DEFAULT_MANIFEST_FILENAME
