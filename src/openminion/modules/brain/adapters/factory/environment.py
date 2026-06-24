"""Environment helpers for brain adapter factories."""

import sys
from pathlib import Path
from typing import Callable

from openminion.base.config import resolve_data_root, resolve_home_root
from openminion.base.config.env import resolve_environment_config


def env_bool(name: str, default: bool = True) -> bool:
    raw = resolve_environment_config().get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def context_feature_flags(*, env_bool: Callable[[str, bool], bool]) -> dict[str, bool]:
    return {
        "rolling_enabled": env_bool("OPENMINION_CONTEXT_ROLLING_ENABLED", True),
        "compaction_enabled": env_bool("OPENMINION_CONTEXT_COMPACTION_ENABLED", True),
        "compression_enabled": env_bool("OPENMINION_CONTEXT_COMPRESSION_ENABLED", True),
    }


def default_data_root() -> Path:
    home_root = resolve_home_root()
    return resolve_data_root(
        home_root,
        data_root=resolve_environment_config().openminion_data_root or None,
    )


def ensure_a2a_dependency_available() -> None:
    try:
        import openminion.modules.a2a  # noqa: F401
    except ModuleNotFoundError:
        root = Path(__file__).resolve().parents[5]
        candidate = root / "openminion" / "src"
        if candidate.exists() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
        import openminion.modules.a2a  # noqa: F401


__all__ = [
    "context_feature_flags",
    "default_data_root",
    "ensure_a2a_dependency_available",
    "env_bool",
]
