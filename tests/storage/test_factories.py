from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.storage.record_store import RecordStoreSQLite

from tests.storage import factories
from tests.storage.factories import FACTORIES, reset_seed


_SCHEMAS: dict[str, tuple[str, str]] = {
    "secret": (
        "secrets",
        """
        CREATE TABLE secrets (
            key TEXT NOT NULL,
            namespace TEXT NOT NULL DEFAULT 'default',
            value TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (key, namespace)
        )
        """,
    ),
    "session": (
        "sessions",
        """
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            title TEXT,
            status TEXT NOT NULL,
            active_agent_id TEXT,
            participants_json TEXT NOT NULL DEFAULT '[]',
            root_goal TEXT,
            tags_json TEXT NOT NULL DEFAULT '[]',
            config_snapshot_ref TEXT,
            meta_json TEXT NOT NULL DEFAULT '{}'
        )
        """,
    ),
    "memory": (
        "memory_records",
        """
        CREATE TABLE memory_records (
            id TEXT PRIMARY KEY,
            scope TEXT NOT NULL,
            type TEXT NOT NULL,
            key TEXT,
            title TEXT,
            content_json TEXT NOT NULL,
            tags_json TEXT NOT NULL DEFAULT '[]',
            entities_json TEXT NOT NULL DEFAULT '[]',
            source TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '[]',
            meta_json TEXT NOT NULL DEFAULT '{}',
            last_hit_at TEXT,
            tier TEXT NOT NULL DEFAULT 'working',
            access_count INTEGER NOT NULL DEFAULT 0,
            expires_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            supersedes_id TEXT,
            superseded_by_id TEXT
        )
        """,
    ),
    "telemetry": (
        "events",
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            timestamp REAL NOT NULL,
            data TEXT NOT NULL
        )
        """,
    ),
    "a2a": (
        "agents",
        """
        CREATE TABLE agents (
            agent_id TEXT PRIMARY KEY,
            capabilities_json TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
    ),
    "artifact": (
        "artifacts",
        """
        CREATE TABLE artifacts (
            sha256 TEXT PRIMARY KEY,
            size_bytes INTEGER NOT NULL,
            mime TEXT NOT NULL,
            created_at TEXT NOT NULL,
            original_name TEXT,
            original_path TEXT,
            label TEXT,
            session_id TEXT,
            trace_id TEXT,
            agent_id TEXT,
            encoding TEXT,
            deleted_at TEXT,
            meta_json TEXT
        )
        """,
    ),
    "controlplane": (
        "cp_principals",
        """
        CREATE TABLE cp_principals (
            principal_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            meta_json TEXT NOT NULL DEFAULT '{}'
        )
        """,
    ),
    "identity": (
        "identity_profiles",
        """
        CREATE TABLE identity_profiles (
            agent_id TEXT PRIMARY KEY,
            profile_json TEXT NOT NULL,
            profile_revision INTEGER NOT NULL,
            profile_version TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
    ),
    "policy": (
        "policy_grants",
        """
        CREATE TABLE policy_grants (
            grant_id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            effect TEXT NOT NULL,
            tool TEXT NOT NULL,
            method TEXT NOT NULL,
            target_json TEXT NOT NULL DEFAULT '{}',
            risk_floor TEXT,
            duration_type TEXT NOT NULL,
            expires_at TEXT,
            session_id TEXT,
            invocation_hash TEXT,
            max_uses INTEGER,
            uses_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            revoked_at TEXT,
            reason TEXT,
            created_trace_id TEXT
        )
        """,
    ),
    "registry": (
        "agents",
        """
        CREATE TABLE agents (
            agent_id TEXT PRIMARY KEY,
            descriptor_json TEXT NOT NULL,
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
    ),
    "retrieve": (
        "retrievectl_docs",
        """
        CREATE TABLE retrievectl_docs (
            doc_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_ref TEXT NOT NULL
        )
        """,
    ),
    "skill": (
        "skills",
        """
        CREATE TABLE skills (
            skill_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            scope TEXT NOT NULL,
            agent_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
    ),
    "task": (
        "tasks",
        """
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL,
            due_at TEXT,
            scheduled_at TEXT,
            wait_at TEXT,
            created_by_mode TEXT,
            executing_mode TEXT,
            current_plan_id TEXT,
            next_step_id TEXT,
            created_at TEXT NOT NULL
        )
        """,
    ),
    "storage": (
        "om_meta",
        """
        CREATE TABLE om_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """,
    ),
}


def test_factory_count_matches_smp25_exit_criterion() -> None:

    assert len(FACTORIES) == 14
    assert set(FACTORIES.keys()) == set(_SCHEMAS.keys())


@pytest.mark.parametrize("module_name", sorted(FACTORIES.keys()))
def test_factory_returns_dict_with_expected_shape(module_name: str) -> None:

    factory = FACTORIES[module_name]
    row = factory()
    assert isinstance(row, dict)
    assert row, f"{module_name} factory returned empty dict"


@pytest.mark.parametrize("module_name", sorted(FACTORIES.keys()))
def test_factory_row_inserts_into_module_schema(
    module_name: str, tmp_path: Path
) -> None:

    table_name, ddl = _SCHEMAS[module_name]
    db_path = tmp_path / f"{module_name}.db"
    store = RecordStoreSQLite(db_path)
    try:
        store.execute_count(ddl)
        row = FACTORIES[module_name]()
        # ``telemetry.events`` uses INTEGER PRIMARY KEY AUTOINCREMENT for
        # ``id``; the factory does not include it so SQLite assigns one.
        store.insert(table_name, row)
        # Round-trip the row to confirm at least one row landed.
        rows = store.query_dicts(f'SELECT * FROM "{table_name}"')
        assert len(rows) == 1
    finally:
        store.close()


@pytest.mark.parametrize("module_name", sorted(FACTORIES.keys()))
def test_factory_overrides_apply(module_name: str) -> None:

    factory = FACTORIES[module_name]
    base_row = factory()
    # Pick a column that the factory generates with a non-None value.
    override_key = next(
        (k for k, v in base_row.items() if v is not None and isinstance(v, str)),
        None,
    )
    if override_key is None:
        pytest.skip(f"{module_name} factory has no overridable string column")
    sentinel = "OVERRIDE-SENTINEL-XYZ"
    overridden = factory(**{override_key: sentinel})
    assert overridden[override_key] == sentinel


def test_factories_are_deterministic_with_reset_seed() -> None:

    reset_seed(42)
    first_pass = {name: factory() for name, factory in FACTORIES.items()}
    reset_seed(42)
    second_pass = {name: factory() for name, factory in FACTORIES.items()}
    assert first_pass == second_pass


def test_factories_module_exports_are_callable() -> None:

    for name in factories.__all__:
        attr = getattr(factories, name)
        if name in {"FACTORIES", "reset_seed"}:
            continue
        # All other exports are factory functions returning dicts.
        result = attr()
        assert isinstance(result, dict), f"{name} did not return a dict"
