# ruff: noqa: E402

from pathlib import Path
import sys
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOPHIAGRAPH_SRC = _REPO_ROOT / "sophiagraph" / "src"
if _SOPHIAGRAPH_SRC.exists():
    sys.path.insert(0, str(_SOPHIAGRAPH_SRC))

from openminion.base.config import ConfigManager, OpenMinionConfig
from openminion.services.bootstrap.config import bootstrap_config_manager


@pytest.fixture
def fresh_config_manager(tmp_path):
    manager = ConfigManager(
        base_config=OpenMinionConfig(),
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
        config_path=tmp_path / "config.json",
    )
    bootstrap_config_manager(manager)
    return manager


@pytest.fixture(autouse=True)
def _force_isolated_test_roots(monkeypatch, tmp_path):
    """Point ordinary tests at temporary OpenMinion roots."""
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))
    # Allow tmp_path-backed databases inside isolated test roots.
    monkeypatch.setenv("OPENMINION_DATA_ROOT_ENFORCEMENT", "soft")
