from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from openminion.modules.storage.record_store import RecordStore
from openminion.modules.storage.migrations.module_ids import MODULE_APPLICATION_IDS
from .task_tables import migrate_v1_to_v2

POSTGRES_VALIDATED_MODULES: frozenset[str] = frozenset(
    {
        "secret",
        "session",
        "telemetry",
        "identity",
        "registry",
        "task",
        "skill",
        "controlplane",
        "policy",
        "compress",
        "retrieve",
        "artifact",
        "a2a",
        "memory",
    }
)


class ModuleLifecycle(str, Enum):
    ACTIVE = "active"
    ADDED = "added"
    DEPRECATED = "deprecated"
    REMOVED = "removed"


class MigrationAction(str, Enum):
    INIT_AND_MIGRATE = "init_and_migrate"
    MIGRATE = "migrate"
    VERIFY_ONLY = "verify_only"
    EXPORT_AND_ARCHIVE = "export_and_archive"
    REHYDRATE = "rehydrate"
    SKIP = "skip"


@dataclass(frozen=True)
class ModuleMigrationSpec:
    module_id: str
    module_application_id: int
    dependencies: tuple[str, ...] = ()
    lifecycle: ModuleLifecycle = ModuleLifecycle.ACTIVE
    is_installed: bool = True
    has_db: bool = True
    supports_omx: bool = True

    def apply_migration(
        self, store: RecordStore, from_version: int, to_version: int
    ) -> None:
        if from_version < 1 and to_version >= 2:
            migrate_v1_to_v2(store)


MODULE_SPECS = [
    ModuleMigrationSpec(
        module_id=module_id,
        module_application_id=application_id,
        lifecycle=ModuleLifecycle.ACTIVE,
    )
    for module_id, application_id in MODULE_APPLICATION_IDS.items()
]

MODULE_REGISTRY = {spec.module_id: spec for spec in MODULE_SPECS}


def get_module_spec(module_id: str) -> ModuleMigrationSpec | None:
    return MODULE_REGISTRY.get(module_id)


def build_migration_plan(specs: list[ModuleMigrationSpec]) -> list[MigrationPlanItem]:
    by_id = {spec.module_id: spec for spec in specs}
    ordered_ids = _topological_order(specs)
    plan: list[MigrationPlanItem] = []

    for module_id in ordered_ids:
        spec = by_id[module_id]

        if spec.lifecycle == ModuleLifecycle.REMOVED:
            if spec.has_db and spec.supports_omx:
                plan.append(
                    MigrationPlanItem(
                        module_id=module_id,
                        action=MigrationAction.EXPORT_AND_ARCHIVE,
                        reason="module removed; export DB to OMX and archive for future recovery",
                    )
                )
            else:
                plan.append(
                    MigrationPlanItem(
                        module_id=module_id,
                        action=MigrationAction.SKIP,
                        reason="module removed with no DB/export support",
                    )
                )
            continue

        if spec.lifecycle == ModuleLifecycle.ADDED:
            plan.append(
                MigrationPlanItem(
                    module_id=module_id,
                    action=MigrationAction.INIT_AND_MIGRATE,
                    reason="new module in target; initialize DB and migrate to head",
                )
            )
            continue

        if not spec.has_db:
            plan.append(
                MigrationPlanItem(
                    module_id=module_id,
                    action=MigrationAction.SKIP,
                    reason="no module DB present",
                )
            )
            continue

        if spec.lifecycle == ModuleLifecycle.DEPRECATED:
            plan.append(
                MigrationPlanItem(
                    module_id=module_id,
                    action=MigrationAction.VERIFY_ONLY,
                    reason="module deprecated; keep data readable and verified",
                )
            )
            continue

        plan.append(
            MigrationPlanItem(
                module_id=module_id,
                action=MigrationAction.MIGRATE,
                reason="active module migration",
            )
        )

    return plan


@dataclass(frozen=True)
class MigrationPlanItem:
    module_id: str
    action: MigrationAction
    reason: str


def _topological_order(specs: list[ModuleMigrationSpec]) -> list[str]:
    graph: dict[str, set[str]] = {
        spec.module_id: set(spec.dependencies) for spec in specs
    }
    all_ids = set(graph.keys())

    for module_id, deps in graph.items():
        unknown = deps - all_ids
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"Unknown dependencies for {module_id}: {names}")

    indegree: dict[str, int] = {module_id: 0 for module_id in all_ids}
    reverse: dict[str, set[str]] = {module_id: set() for module_id in all_ids}

    for module_id, deps in graph.items():
        for dep in deps:
            indegree[module_id] += 1
            reverse[dep].add(module_id)

    ready = sorted([module_id for module_id, value in indegree.items() if value == 0])
    ordered: list[str] = []

    while ready:
        current = ready.pop(0)
        ordered.append(current)

        for nxt in sorted(reverse[current]):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
        ready.sort()

    if len(ordered) != len(all_ids):
        raise ValueError("Dependency cycle detected in module migration registry")

    return ordered
