import sqlite3
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from openminion.base.config.env import (
    EnvironmentConfig,
    resolve_environment_config_with_explicit_env,
)
from openminion.base.config.paths import ensure_under_data_root
from openminion.modules.config import (
    resolve_module_data_root,
    resolve_module_home_root,
)
from openminion.modules.storage.record_store import configure_connection


DEFAULT_DATABASE_PATH = Path("state") / "openminion.db"


class StorageError(RuntimeError):
    """Raised when sqlite storage cannot be initialized."""


def resolve_database_path(
    database_path: str | Path | None,
    *,
    env: EnvironmentConfig | Mapping[str, Any] | None = None,
) -> Path:
    resolved_env = resolve_environment_config_with_explicit_env(env)
    resolved_home_root = (
        resolve_module_home_root(
            None,
            resolved_env,
            fallback_to_cwd=True,
        )
        or Path.cwd()
    )
    resolved_data_root = resolve_module_data_root(
        home_root=resolved_home_root,
        env=resolved_env,
    )

    if database_path is None:
        candidate = resolved_data_root / DEFAULT_DATABASE_PATH
    else:
        candidate = Path(database_path).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        candidate = resolved_data_root / candidate

    return ensure_under_data_root(
        candidate, resolved_data_root, label="database_path"
    ).resolve()


def connect_database(
    database_path: str | Path,
    *,
    env: EnvironmentConfig | Mapping[str, Any] | None = None,
) -> sqlite3.Connection:
    path = resolve_database_path(database_path, env=env)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(path), check_same_thread=False)
    except OSError as exc:
        raise StorageError(f"Unable to prepare storage path at {path}: {exc}") from exc
    except sqlite3.Error as exc:
        raise StorageError(f"Unable to open sqlite database at {path}: {exc}") from exc

    connection.row_factory = sqlite3.Row
    configure_connection(connection, wal=True)
    return connection
