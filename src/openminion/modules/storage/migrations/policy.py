from dataclasses import dataclass

from openminion.modules.storage.migrations.errors import DbVersionError
from openminion.modules.storage.migrations.registry import MigrationAction


@dataclass(frozen=True)
class DataCompatWindow:
    min_data_version: int
    max_data_version: int


def requires_rehydrate(
    *, user_version: int, window: DataCompatWindow, has_migration_path: bool
) -> bool:
    in_window = window.min_data_version <= user_version <= window.max_data_version
    if in_window and has_migration_path:
        return False
    return True


def resolve_version_action(
    *,
    user_version: int,
    window: DataCompatWindow,
    has_migration_path: bool,
    supports_omx: bool,
) -> MigrationAction:
    if not requires_rehydrate(
        user_version=user_version, window=window, has_migration_path=has_migration_path
    ):
        return MigrationAction.MIGRATE

    if supports_omx:
        return MigrationAction.REHYDRATE

    raise DbVersionError(
        "DB version is outside supported migration path and OMX fallback is not available. "
        f"user_version={user_version}, window={window.min_data_version}..{window.max_data_version}"
    )
