from dataclasses import dataclass
from pathlib import Path

from openminion.modules.storage.migrations.alembic import (
    run_module_alembic_migrations,
)
from openminion.modules.storage.migrations.module_ids import get_module_application_id

from ..schema import (
    BOOTSTRAP_SCHEMA,
    CRON_SCHEMA,
    EVENT_SOURCED_SCHEMA,
    SESSION_RETENTION_SCHEMA,
    SESSION_SHARING_SCHEMA,
    SESSION_CONTINUATION_SCHEMA,
    V15_SCHEMA,
)


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "bootstrap_sessctl_v1", BOOTSTRAP_SCHEMA),
    Migration(2, "event_sourced_sessions_v1", EVENT_SOURCED_SCHEMA),
    Migration(3, "cron_jobs_and_runs_v1", CRON_SCHEMA),
    Migration(4, "v1_5_continuity_and_canonical_events", V15_SCHEMA),
    Migration(5, "session_continuation_lineage_index", SESSION_CONTINUATION_SCHEMA),
    Migration(6, "session_sharing_v1", SESSION_SHARING_SCHEMA),
    Migration(7, "session_retention_v1", SESSION_RETENTION_SCHEMA),
)

MODULE_ID = "session"
MODULE_APPLICATION_ID = get_module_application_id(MODULE_ID)
TARGET_USER_VERSION = 0
BASELINE_REVISION = "0001_baseline"
LEGACY_MIGRATIONS: tuple[str, ...] = tuple(
    f"{migration.version:04d}_{migration.name}" for migration in MIGRATIONS
)


def run_migrations(db_path: str | Path) -> None:
    run_module_alembic_migrations(
        module_id=MODULE_ID,
        db_path=db_path,
        target_user_version=TARGET_USER_VERSION,
    )


def list_migrations() -> list[str]:
    return [
        *LEGACY_MIGRATIONS,
        BASELINE_REVISION,
        "0002_run_invocation",
        "0003_cron_coordination",
    ]
