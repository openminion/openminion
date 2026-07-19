from __future__ import annotations

from typing import Any

from openminion.base.config import OpenMinionConfig
from openminion.cli.config import load_cli_manager
from openminion.modules.brain.paths import resolve_brain_sessions_db_path
from openminion.modules.session.runtime.factory import build_module_session_store
from openminion.modules.storage.engine import StorageEngineConfig
from openminion.modules.storage.runtime.sqlite import resolve_database_path


def build_status_session_store(args: Any, config: OpenMinionConfig) -> Any:
    manager = load_cli_manager(args.config)
    storage_env = manager.env.snapshot()
    storage_env.setdefault("OPENMINION_HOME", str(manager.home_root))
    storage_env.setdefault("OPENMINION_DATA_ROOT", str(manager.data_root))
    storage_path = resolve_database_path(config.storage.path, env=storage_env)
    session_path = resolve_brain_sessions_db_path(storage_path=storage_path)
    return build_module_session_store(
        config=StorageEngineConfig(
            root_dir=session_path.parent,
            sqlite_path=session_path,
            fallback_root=session_path.parent,
            record_backend=config.storage.record_backend(),
            record_backend_options=config.storage.record_backend_options(),
        ),
        database_path=session_path,
        env=manager.env,
    )


__all__ = ["build_status_session_store"]
