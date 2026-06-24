from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from openminion.modules.storage.migrations.module_ids import get_module_application_id
from openminion.modules.storage.migrations.runner import MigrationRunner


ONBOARDED_MODULES = [
    "session",
    "secret",
    "skill",
    "registry",
    "artifact",
    "identity",
    "telemetry",
    "task",
    "policy",
    "retrieve",
    "a2a",
    "controlplane",
    "controlplane_telegram",
    "memory",
    "authoring",
]


MODULE_IMPORT_PATHS: dict[str, str] = {
    "controlplane_telegram": "openminion.modules.controlplane.channels.telegram.storage.migrations",
    "authoring": "openminion.modules.tool.authoring.storage.migrations",
}


@pytest.mark.parametrize("module_id", ONBOARDED_MODULES)
def test_module_run_migrations_bootstraps_alembic_baseline(
    tmp_path: Path, module_id: str
) -> None:
    db_path = tmp_path / f"{module_id}.db"
    import_path = MODULE_IMPORT_PATHS.get(
        module_id, f"openminion.modules.{module_id}.storage.migrations"
    )
    migrations = importlib.import_module(import_path)

    migrations.run_migrations(db_path)

    runner = MigrationRunner(
        module_id=module_id,
        db_path=db_path,
        module_application_id=get_module_application_id(module_id),
    )
    state = runner.detect()
    declared = migrations.list_migrations()
    schema_head = declared[-1]

    assert state.exists is True
    assert state.application_id == get_module_application_id(module_id)
    assert state.application_id_matches is True
    assert state.alembic_revision == schema_head
    assert state.om_meta["schema_head"] == schema_head
    assert "0001_baseline" in declared
    assert declared[-1] == schema_head
