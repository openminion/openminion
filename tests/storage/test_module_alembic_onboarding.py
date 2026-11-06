from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from openminion.modules.storage.migrations.alembic import (
    discover_module_alembic_paths,
)
from openminion.modules.storage.migrations.module_ids import get_module_application_id
from openminion.modules.storage.migrations.registry import POSTGRES_VALIDATED_MODULES
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
    "compress",
]


MODULE_IMPORT_PATHS: dict[str, str] = {
    "controlplane_telegram": "openminion.modules.controlplane.channels.telegram.storage.migrations",
    "authoring": "openminion.modules.tool.authoring.storage.migrations",
    "compress": "openminion.modules.context.compress.storage.migrations",
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


def test_discovery_finds_the_compress_storage_package() -> None:
    storage = importlib.import_module("openminion.modules.context.compress.storage")
    storage_root = Path(storage.__file__).resolve().parent

    ini_path, script_location = discover_module_alembic_paths("compress")

    assert script_location == storage_root / "migrations"
    assert ini_path == storage_root / "alembic.ini"


def test_every_postgres_validated_module_has_a_script_location() -> None:
    # `storage migrate --backend postgres` runs these modules and finds their
    # migrations through discovery; this catches a moved package without a database.
    missing = [
        module_id
        for module_id in sorted(POSTGRES_VALIDATED_MODULES)
        if discover_module_alembic_paths(module_id)[1] is None
    ]

    assert missing == []
