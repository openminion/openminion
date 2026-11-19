"""Database path resolution and empty-string storage bootstrap contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from openminion import APIRuntime
from openminion.base.config import OpenMinionConfig, load_config, save_config
from openminion.base.config.base import ConfigError
from openminion.base.config.core import StorageConfig
from openminion.modules.storage.runtime.context import build_runtime_storage
from openminion.modules.storage.runtime.sqlite import (
    connect_database,
    resolve_database_path,
)
from tests._csc_fixtures import _csc_install_default_agent


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "OPENMINION_HOME": str(tmp_path / "home"),
        "OPENMINION_DATA_ROOT": str(tmp_path / "data"),
    }


@pytest.mark.parametrize("omitted_path", [None, ""])
def test_resolve_database_path_treats_empty_string_like_none(
    tmp_path: Path, omitted_path: str | None
) -> None:
    resolved = resolve_database_path(omitted_path, env=_env(tmp_path))

    assert resolved == (tmp_path / "data" / "state" / "openminion.db").resolve()


def test_config_empty_storage_path_bootstraps_default_sqlite_storage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    data_root = root / ".openminion"
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")
    config.runtime.process_mode = "single-process"
    config.runtime.daemon_auto_start = False
    config.storage = StorageConfig(path="")
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "agents.json"
    save_config(config, str(config_path))

    loaded = load_config(str(config_path))
    assert loaded.storage.path == ""

    expected_db = (data_root / "state" / "openminion.db").resolve()
    storage = build_runtime_storage(
        loaded.storage.path,
        env={"OPENMINION_HOME": str(root), "OPENMINION_DATA_ROOT": str(data_root)},
    )
    try:
        assert storage.sqlite_path == expected_db
        assert expected_db.is_file()
    finally:
        storage.close()

    runtime = APIRuntime.from_config_path(
        str(config_path), home_root=root, data_root=data_root
    )
    try:
        assert runtime.runtime_storage.sqlite_path == expected_db
    finally:
        runtime.close()


def test_whitespace_database_filenames_remain_intact(tmp_path: Path) -> None:
    env = _env(tmp_path)
    data_root = (tmp_path / "data").resolve()

    whitespace_only = resolve_database_path("   ", env=env)
    assert whitespace_only == data_root / "   "

    connection = connect_database("   ", env=env)
    try:
        connection.execute("CREATE TABLE probe (value TEXT)")
        connection.commit()
    finally:
        connection.close()
    assert (data_root / "   ").is_file()

    spaced_name = resolve_database_path("state/my notes.db", env=env)
    assert spaced_name == (data_root / "state" / "my notes.db").resolve()


def test_explicit_relative_absolute_path_and_containment_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(tmp_path)
    data_root = (tmp_path / "data").resolve()
    outside_db = (tmp_path / "outside" / "explicit.db").resolve()

    assert (
        resolve_database_path("state/custom.db", env=env)
        == (data_root / "state" / "custom.db").resolve()
    )
    assert (
        resolve_database_path(Path("state/from_path.db"), env=env)
        == (data_root / "state" / "from_path.db").resolve()
    )
    assert resolve_database_path(Path(""), env=env) == data_root
    assert resolve_database_path(str(outside_db), env=env) == outside_db
    assert resolve_database_path(outside_db, env=env) == outside_db

    monkeypatch.setenv("OPENMINION_DATA_ROOT_ENFORCEMENT", "hard")
    with pytest.raises(ConfigError, match="database_path must be under data_root"):
        resolve_database_path("../escape.db", env=env)
