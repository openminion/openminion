"""Reference external adapter backed by the standalone ``sophiagraph`` SQLite engine."""

from pathlib import Path
from typing import Any

from sophiagraph.storage import SophiaGraphSqliteStore

from openminion.modules.memory.errors import InvalidArgumentError

from .registry import ExternalBackendCapabilities, register_external_backend

REFERENCE_SQLITE_ADAPTER_NAME = "reference-sqlite"


def build_reference_sqlite_backend(*, config: Any, **_: Any) -> SophiaGraphSqliteStore:
    options = getattr(config, "options", {})
    if not isinstance(options, dict):
        options = {}
    raw_path = options.get("db_path") or options.get("sqlite_path")
    if not raw_path:
        raise InvalidArgumentError(
            "reference-sqlite external adapter requires memory.backend.options.db_path"
        )
    return SophiaGraphSqliteStore(Path(str(raw_path)))


def register_reference_sqlite_backend(
    name: str = REFERENCE_SQLITE_ADAPTER_NAME,
) -> None:
    register_external_backend(
        name,
        factory=build_reference_sqlite_backend,
        capabilities=ExternalBackendCapabilities(
            supports_relations=True,
            supports_candidate_workflow=True,
            supports_tier_history=True,
            supports_portability=True,
            supports_semantic_search=False,
        ),
    )
