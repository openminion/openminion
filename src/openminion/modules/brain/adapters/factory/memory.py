from pathlib import Path
from typing import Any

from openminion.modules.brain.constants import (
    DEFAULT_MEMORY_DB_FILENAME,
    DEFAULT_MEMORY_DB_SUBPATH,
)

from .artifactctl import resolve_artifactctl
from .environment import default_data_root
from .modes import mode_is_local, raise_if_strict


def _config_value(config: Any, *keys: str) -> Any:
    current = config
    for key in keys:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(key)
        else:
            current = getattr(current, key, None)
    return current


def create_memory_adapter(
    mode: str = "auto",
    db_path: str | Path | None = None,
    vector_adapter: Any = None,
    *,
    config: Any = None,
    artifactctl: Any | None = None,
    telemetryctl: Any | None = None,
    agent_id: str | None = None,
) -> Any:
    from openminion.modules.brain.adapters.memory import LocalMemoryAdapter

    resolved_path: Path | None = None
    if db_path is not None:
        resolved_path = Path(db_path)
    elif config is not None:
        sqlite_path = _config_value(config, "sqlite_path") or _config_value(
            config, "store", "sqlite_path"
        )
        if sqlite_path:
            resolved_path = Path(str(sqlite_path)).expanduser()
    if resolved_path is None:
        resolved_path = default_data_root() / DEFAULT_MEMORY_DB_SUBPATH

    if resolved_path.suffix:
        base_dir = resolved_path.parent
        sqlite_path = resolved_path
    else:
        base_dir = resolved_path
        sqlite_path = resolved_path / DEFAULT_MEMORY_DB_FILENAME
    if mode_is_local(mode):
        return LocalMemoryAdapter(base_dir)
    runtime_artifactctl = resolve_artifactctl(artifactctl=artifactctl)
    try:
        from openminion.modules.memory.service import MemoryService
        from openminion.modules.memory.storage import (
            AuditedMemoryStore,
            SQLiteMemoryAuditSink,
            default_memory_audit_db_path,
        )
        from openminion.modules.memory.storage.factory import resolve_memory_backend
        from ..memory import MemctlAdapter

        resolved_backend = resolve_memory_backend(
            config=config,
            db_path=sqlite_path,
            artifactctl=runtime_artifactctl,
        )
        audited_store = AuditedMemoryStore(
            resolved_backend.store,
            sink=SQLiteMemoryAuditSink(default_memory_audit_db_path(sqlite_path)),
        )
        memory_service = MemoryService(
            store=audited_store,
            vector_adapter=vector_adapter,
            policy_config=config,
            telemetryctl=telemetryctl,
        )
        learning_cfg = _config_value(config, "candidate_learning")
        if learning_cfg is not None and hasattr(
            memory_service, "set_candidate_learning_config"
        ):
            try:
                memory_service.set_candidate_learning_config(learning_cfg)
            except Exception:
                pass
        retention_cfg = _config_value(config, "retention")
        if retention_cfg is not None and hasattr(memory_service, "set_tiering_config"):
            try:
                memory_service.set_tiering_config(retention_cfg)
            except Exception:
                pass
        return MemctlAdapter(memory_service, agent_id=agent_id)
    except ImportError:
        raise_if_strict(mode)
        return LocalMemoryAdapter(base_dir)
