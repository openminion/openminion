from pathlib import Path

from openminion.modules.storage.migrations.alembic import (
    run_module_alembic_migrations,
)
from openminion.modules.storage.migrations.module_ids import get_module_application_id

MODULE_ID = "memory"
MODULE_APPLICATION_ID = get_module_application_id(MODULE_ID)
TARGET_USER_VERSION = 0
BASELINE_REVISION = "0001_baseline"
MIGRATIONS = (
    BASELINE_REVISION,
    "0002_audit",
    "0003_validity",
    "0004_namespace",
)


def run_migrations(db_path: str | Path) -> None:
    run_module_alembic_migrations(
        module_id=MODULE_ID,
        db_path=db_path,
        target_user_version=TARGET_USER_VERSION,
    )


def list_migrations() -> list[str]:
    return list(MIGRATIONS)
