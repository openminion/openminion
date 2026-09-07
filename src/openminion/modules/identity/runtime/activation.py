"""Identity source checks used during runtime activation."""

from pathlib import Path

from openminion.base.config.env import EnvironmentConfig
from openminion.base.constants import (
    OPENMINION_IDENTITY_DB_ENV,
    OPENMINION_IDENTITY_ROOT_ENV,
)
from openminion.modules.identity.storage.store import SQLiteIdentityStore


def has_explicit_identity_config(env: EnvironmentConfig, config: object) -> bool:
    return any(
        str(value or "").strip()
        for value in (
            env.get(OPENMINION_IDENTITY_DB_ENV, ""),
            env.get(OPENMINION_IDENTITY_ROOT_ENV, ""),
            getattr(config, "db_path", ""),
            getattr(config, "bundle_root", ""),
            getattr(config, "root", ""),
        )
    )


def current_agent_identity_exists(
    *, agent_id: str, identity_root: Path, db_path: Path
) -> bool:
    if (identity_root / agent_id / "profile.yaml").is_file():
        return True
    if not db_path.is_file():
        return False

    store = SQLiteIdentityStore(sqlite_path=str(db_path))
    try:
        return store.get_profile(agent_id) is not None
    finally:
        store.close()


__all__ = ["current_agent_identity_exists", "has_explicit_identity_config"]
