"""Runtime start, room and wrapper scenarios shared by the SQLite and PostgreSQL tests.

Both build the same echo-agent runtime home and differ only in its `storage`
section. `storage_connection` and the wrapper's `storage` are the SQLite handle
of the record store, and `None` for other backends.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openminion import APIRuntime
from openminion.base.config import OpenMinionConfig, save_config
from openminion.base.config.core import StorageConfig
from openminion.cli.main import main
from openminion.services.runtime import OpenMinionRuntime
from tests._csc_fixtures import _csc_install_default_agent

AGENT_ID = "openminion"
ROOM_NAME = "storage-room"


def state_db(root: Path) -> Path:
    """The runtime SQLite database path that `storage.path` names."""
    return root / ".openminion" / "state.db"


def write_runtime_config(root: Path, *, postgres_url: str | None = None) -> Path:
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")
    config.runtime.process_mode = "single-process"
    config.runtime.daemon_auto_start = False
    if postgres_url is None:
        config.storage = StorageConfig(path=str(state_db(root)))
    else:
        config.storage = StorageConfig(
            path=str(state_db(root)), backend="postgres", postgres_url=postgres_url
        )
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "agents.json"
    save_config(config, str(config_path))
    return config_path


def start_runtime(config_path: Path) -> APIRuntime:
    root = config_path.parent
    return APIRuntime.from_config_path(
        str(config_path), home_root=root, data_root=root / ".openminion"
    )


def start_wrapper(config_path: Path) -> OpenMinionRuntime:
    root = config_path.parent
    return OpenMinionRuntime.from_config_path(
        str(config_path), home_root=str(root), data_root=str(root / ".openminion")
    )


def room_create(
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, dict[str, str]]:
    """Run `openminion room create` in process and return its exit and summary."""
    root = config_path.parent
    data_root = root / ".openminion"
    # main() exports the roots it resolves; setting them first scopes them to the test.
    monkeypatch.setenv("OPENMINION_HOME", str(root))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    monkeypatch.setenv(
        "OPENMINION_GENERATED_ROOT", str((data_root / "runtime").resolve())
    )
    capsys.readouterr()
    exit_code = main(
        [
            "--config",
            str(config_path),
            "--home-root",
            str(root),
            "--data-root",
            str(data_root),
            "room",
            "create",
            "--name",
            ROOM_NAME,
            "--agent",
            AGENT_ID,
        ]
    )
    summary = dict(
        line.split("=", 1)
        for line in capsys.readouterr().out.splitlines()
        if "=" in line
    )
    return exit_code, summary
