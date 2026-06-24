from __future__ import annotations

import pytest

from openminion.modules.storage.migrations import (
    MigrationAction,
    ModuleLifecycle,
    ModuleMigrationSpec,
    build_migration_plan,
)


def test_registry_plan_orders_by_dependencies_and_actions():
    specs = [
        ModuleMigrationSpec(
            module_id="openminion-storage", module_application_id=0x4F4D0000
        ),
        ModuleMigrationSpec(
            module_id="openminion-session",
            module_application_id=0x4F4D0001,
            dependencies=("openminion-storage",),
            lifecycle=ModuleLifecycle.ADDED,
        ),
        ModuleMigrationSpec(
            module_id="openminion-artifact",
            module_application_id=0x4F4D0002,
            dependencies=("openminion-storage",),
        ),
    ]

    plan = build_migration_plan(specs)

    assert [item.module_id for item in plan] == [
        "openminion-storage",
        "openminion-artifact",
        "openminion-session",
    ]
    assert plan[0].action == MigrationAction.MIGRATE
    assert plan[1].action == MigrationAction.MIGRATE
    assert plan[2].action == MigrationAction.INIT_AND_MIGRATE


def test_registry_plan_exports_removed_modules_with_db():
    specs = [
        ModuleMigrationSpec(
            module_id="openminion-storage", module_application_id=0x4F4D0000
        ),
        ModuleMigrationSpec(
            module_id="openminion-legacy-module",
            module_application_id=0x4F4D00AA,
            lifecycle=ModuleLifecycle.REMOVED,
            has_db=True,
            supports_omx=True,
        ),
    ]

    plan = build_migration_plan(specs)
    removed = [item for item in plan if item.module_id == "openminion-legacy-module"][0]

    assert removed.action == MigrationAction.EXPORT_AND_ARCHIVE


def test_registry_plan_rejects_dependency_cycles():
    specs = [
        ModuleMigrationSpec(
            module_id="openminion-a",
            module_application_id=0x4F4D0101,
            dependencies=("openminion-b",),
        ),
        ModuleMigrationSpec(
            module_id="openminion-b",
            module_application_id=0x4F4D0102,
            dependencies=("openminion-a",),
        ),
    ]

    with pytest.raises(ValueError, match="cycle"):
        build_migration_plan(specs)
