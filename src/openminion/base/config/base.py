from __future__ import annotations

from pathlib import Path

from openminion.base.constants import (
    BASE_DEFAULT_CONFIG_DIRNAME,
    BASE_DEFAULT_CONFIG_FILENAME,
    BASE_STATE_DB_FILENAME,
    BASE_STATE_DIRNAME,
)


class ConfigError(RuntimeError):
    """Raised when config cannot be loaded or validated."""


class UnknownProfileError(ConfigError):
    """Raised when a requested agent profile does not exist in the config catalog."""


DEFAULT_CONFIG_DIR = Path(BASE_DEFAULT_CONFIG_DIRNAME)
DEFAULT_CONFIG_FILENAME = BASE_DEFAULT_CONFIG_FILENAME
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / DEFAULT_CONFIG_FILENAME
DEFAULT_STORAGE_PATH = Path(BASE_STATE_DIRNAME) / BASE_STATE_DB_FILENAME

__all__ = [
    "DEFAULT_CONFIG_DIR",
    "DEFAULT_CONFIG_FILENAME",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_STORAGE_PATH",
    "ConfigError",
    "UnknownProfileError",
]
