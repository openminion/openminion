"""Environment helpers for brain adapter factories."""

from pathlib import Path

from openminion.base.config import resolve_data_root, resolve_home_root
from openminion.base.config.env import resolve_environment_config


def context_feature_flags() -> dict[str, bool]:
    env = resolve_environment_config()
    return {
        "rolling_enabled": env.get_bool("OPENMINION_CONTEXT_ROLLING_ENABLED", True),
        "compaction_enabled": env.get_bool(
            "OPENMINION_CONTEXT_COMPACTION_ENABLED", True
        ),
        "compression_enabled": env.get_bool(
            "OPENMINION_CONTEXT_COMPRESSION_ENABLED", True
        ),
    }


def default_data_root() -> Path:
    home_root = resolve_home_root()
    return Path(
        resolve_data_root(
            home_root,
            data_root=resolve_environment_config().openminion_data_root or None,
        )
    )


__all__ = [
    "context_feature_flags",
    "default_data_root",
]
