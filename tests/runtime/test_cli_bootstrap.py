from __future__ import annotations

import os
from pathlib import Path

from openminion.modules.cli_common import apply_home_data_root_env

from .cli_entrypoint_paths import module_cli_entrypoint_path


def test_apply_home_data_root_env_sets_expected_values(monkeypatch) -> None:
    monkeypatch.delenv("OPENMINION_HOME", raising=False)
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)
    home, data = apply_home_data_root_env(
        home_root=Path("/tmp/openminion-home"),
        data_root=" /tmp/openminion-data ",
    )
    assert home == "/tmp/openminion-home"
    assert data == "/tmp/openminion-data"
    assert home == os.environ["OPENMINION_HOME"]
    assert data == os.environ["OPENMINION_DATA_ROOT"]


def test_apply_home_data_root_env_ignores_blank_values(monkeypatch) -> None:
    monkeypatch.setenv("OPENMINION_HOME", "/existing/home")
    monkeypatch.setenv("OPENMINION_DATA_ROOT", "/existing/data")
    home, data = apply_home_data_root_env(home_root="  ", data_root=None)
    assert home is None
    assert data is None
    assert os.environ["OPENMINION_HOME"] == "/existing/home"
    assert os.environ["OPENMINION_DATA_ROOT"] == "/existing/data"


def test_pilot_module_clis_use_shared_bootstrap_helper() -> None:
    root = Path(__file__).resolve().parents[2]
    tool_cli = module_cli_entrypoint_path(
        root / "src" / "openminion" / "modules" / "tool"
    )
    assert tool_cli is not None
    pilots = (
        root / "src" / "openminion" / "modules" / "brain" / "cli.py",
        root / "src" / "openminion" / "modules" / "registry" / "cli.py",
        tool_cli,
    )
    for pilot in pilots:
        text = pilot.read_text(encoding="utf-8")
        assert "from openminion.modules.cli_common import" in text
        assert "apply_home_data_root_env(" in text


def test_module_clis_do_not_mutate_home_data_root_env_inline() -> None:
    root = Path(__file__).resolve().parents[2]
    modules_root = root / "src" / "openminion" / "modules"
    for module_dir in modules_root.iterdir():
        if not module_dir.is_dir() or not (module_dir / "__init__.py").exists():
            continue
        cli_path = module_cli_entrypoint_path(module_dir)
        if cli_path is None:
            continue
        text = cli_path.read_text(encoding="utf-8")
        assert 'os.environ["OPENMINION_HOME"]' not in text
        assert 'os.environ["OPENMINION_DATA_ROOT"]' not in text
