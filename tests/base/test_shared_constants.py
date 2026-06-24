from __future__ import annotations

from pathlib import Path

from openminion.base import constants as base_constants
from openminion.base import generated_paths
from openminion.base.config.base import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_CONFIG_FILENAME,
    DEFAULT_STORAGE_PATH,
)


def test_base_constants_define_canonical_shared_names() -> None:
    assert base_constants.OPENMINION_HOME_ENV == "OPENMINION_HOME"
    assert base_constants.OPENMINION_DATA_ROOT_ENV == "OPENMINION_DATA_ROOT"
    assert base_constants.OPENMINION_GENERATED_ROOT_ENV == "OPENMINION_GENERATED_ROOT"
    assert base_constants.OPENMINION_CONFIG_PATH_ENV == "OPENMINION_CONFIG_PATH"
    assert base_constants.OPENMINION_LOG_LEVEL_ENV == "OPENMINION_LOG_LEVEL"
    assert base_constants.NO_COLOR_ENV == "NO_COLOR"
    assert base_constants.BASE_DEFAULT_CONFIG_DIRNAME == ".openminion"
    assert base_constants.BASE_DEFAULT_CONFIG_FILENAME == "agents.json"
    assert base_constants.BASE_STATE_DIRNAME == "state"
    assert base_constants.BASE_STATE_DB_FILENAME == "openminion.db"


def test_base_config_defaults_derive_from_shared_constants() -> None:
    assert DEFAULT_CONFIG_DIR == Path(base_constants.BASE_DEFAULT_CONFIG_DIRNAME)
    assert DEFAULT_CONFIG_FILENAME == base_constants.BASE_DEFAULT_CONFIG_FILENAME
    assert DEFAULT_STORAGE_PATH == (
        Path(base_constants.BASE_STATE_DIRNAME) / base_constants.BASE_STATE_DB_FILENAME
    )


def test_generated_paths_no_longer_keep_private_env_aliases() -> None:
    assert not hasattr(generated_paths, "_HOME_ENV")
    assert not hasattr(generated_paths, "_DATA_ROOT_ENV")
    assert not hasattr(generated_paths, "_GENERATED_ROOT_ENV")
