import importlib
from pathlib import Path
from typing import Any

from openminion.modules.brain.constants import DEFAULT_SESSION_DB_SUBPATH
from openminion.modules.brain.adapters.session import LocalSessionStore, SessctlAdapter

from .artifactctl import resolve_artifactctl
from .environment import default_data_root
from .modes import mode_is_local, raise_if_strict


def _local_session_root(resolved_path: str | Path | Any) -> Path:
    if isinstance(resolved_path, Path):
        return resolved_path.parent / "sessions"
    return default_data_root() / "session" / "sessions"


def create_session_adapter(
    mode: str = "auto",
    db_path: str | Path | Any | None = None,
    *,
    artifactctl: Any | None = None,
    telemetryctl: Any | None = None,
) -> Any:
    if isinstance(db_path, (str, Path)):
        resolved_path = Path(db_path)
    elif db_path is None:
        resolved_path = default_data_root() / DEFAULT_SESSION_DB_SUBPATH
    else:
        resolved_path = db_path
    local_root = _local_session_root(resolved_path)
    if mode_is_local(mode):
        return LocalSessionStore(local_root)
    runtime_artifactctl = resolve_artifactctl(artifactctl=artifactctl)
    try:
        importlib.import_module("openminion.modules.session")
        return SessctlAdapter(
            db_path if db_path is not None else resolved_path,
            artifactctl=runtime_artifactctl,
            telemetryctl=telemetryctl,
        )
    except ImportError:
        raise_if_strict(mode)
        return LocalSessionStore(local_root)
