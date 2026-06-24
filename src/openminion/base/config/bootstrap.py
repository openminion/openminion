from __future__ import annotations

import os
from pathlib import Path

from openminion.base.constants import (
    OPENMINION_CONFIG_PATH_ENV,
    OPENMINION_DATA_ROOT_ENV,
    OPENMINION_GENERATED_ROOT_ENV,
    OPENMINION_HOME_ENV,
)


def bootstrap_env(
    home_root: Path | str,
    data_root: Path | str | None = None,
    generated_root: Path | str | None = None,
) -> None:
    """Set up OPENMINION_* environment variables for bootstrap."""
    home_root_str = str(home_root)
    os.environ.setdefault(OPENMINION_HOME_ENV, home_root_str)

    if data_root is not None:
        os.environ.setdefault(OPENMINION_DATA_ROOT_ENV, str(data_root))

    if generated_root is not None:
        os.environ.setdefault(OPENMINION_GENERATED_ROOT_ENV, str(generated_root))


def bootstrap_config_path(
    config_path: Path | str | None, *, strict: bool = False
) -> None:
    """Set OPENMINION_CONFIG_PATH through the shared bootstrap path."""
    if config_path is None:
        return
    raw = str(config_path).strip()
    if not raw:
        return
    try:
        resolved = str(Path(raw).expanduser().resolve())
    except OSError:
        resolved = raw
    if strict:
        os.environ[OPENMINION_CONFIG_PATH_ENV] = resolved
        return
    os.environ.setdefault(OPENMINION_CONFIG_PATH_ENV, resolved)


def bootstrap_env_strict(
    home_root: Path | str,
    data_root: Path | str,
    generated_root: Path | str | None = None,
) -> None:
    """Set up OPENMINION_* environment variables (strict, no defaults)."""
    os.environ[OPENMINION_HOME_ENV] = str(home_root)
    os.environ[OPENMINION_DATA_ROOT_ENV] = str(data_root)

    if generated_root is not None:
        os.environ[OPENMINION_GENERATED_ROOT_ENV] = str(generated_root)
