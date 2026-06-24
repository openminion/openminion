from pathlib import Path
from typing import Any

from openminion.modules.brain.constants import DEFAULT_IDENTITY_DB_FILENAME

from .modes import mode_is_local, raise_if_strict


def create_identity_adapter(mode: str = "auto", config: Any = None) -> Any:
    """RIG-05: Create identity adapter for runtime integration."""
    if mode_is_local(mode):
        return None
    try:
        from openminion.modules.identity.runtime.service import IdentityCtl
        from openminion.modules.identity.storage.store import SQLiteIdentityStore

        from openminion.base.config import (
            resolve_module_storage_path,
            resolve_home_root,
        )

        home_root = resolve_home_root()
        default_db = resolve_module_storage_path(
            home_root,
            "identity",
            filename=DEFAULT_IDENTITY_DB_FILENAME,
        )

        db_path_raw = None
        if config is not None:
            if isinstance(config, dict):
                db_path_raw = config.get("db_path") or config.get("sqlite_path")
                storage_cfg = config.get("storage")
                if isinstance(storage_cfg, dict):
                    db_path_raw = db_path_raw or storage_cfg.get("sqlite_path")
            else:
                db_path_raw = getattr(config, "db_path", None) or getattr(
                    config, "sqlite_path", None
                )
                storage_cfg = getattr(config, "storage", None)
                if storage_cfg is not None:
                    db_path_raw = db_path_raw or getattr(
                        storage_cfg, "sqlite_path", None
                    )
        if db_path_raw:
            candidate = Path(str(db_path_raw)).expanduser()
            if not candidate.is_absolute():
                candidate = home_root / candidate
            db_path = candidate.resolve(strict=False)
        else:
            db_path = default_db.resolve(strict=False)

        store = SQLiteIdentityStore(sqlite_path=str(db_path))
        return IdentityCtl(store=store)
    except ImportError:
        raise_if_strict(mode)
        return None
