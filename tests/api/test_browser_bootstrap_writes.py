"""Runtime bootstrap writes no browser artifact directories until a browser is used (#769)."""

from pathlib import Path

import pytest

from openminion import Agent, APIRuntime
from openminion.base.config import OpenMinionConfig, save_config
from openminion.tools.browser.providers.playwright.provider import PlaywrightProvider
from tests._csc_fixtures import _csc_install_default_agent

_OVERRIDE_ENV_KEYS = (
    "OPENMINION_HOME",
    "OPENMINION_DATA_ROOT",
    "OPENMINION_GENERATED_ROOT",
    "OPENMINION_CONFIG_PATH",
)


def test_echo_runtime_bootstrap_creates_no_playwright_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in _OVERRIDE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path / "root"
    monkeypatch.chdir(workspace)

    constructed: list[PlaywrightProvider] = []
    original_init = PlaywrightProvider.__init__

    def record_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        constructed.append(self)

    monkeypatch.setattr(PlaywrightProvider, "__init__", record_init)

    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")
    config.runtime.process_mode = "single-process"
    config.runtime.daemon_auto_start = False
    config.storage.path = str(root / ".openminion" / "state.db")
    config_path = root / "agents.json"
    save_config(config, str(config_path))
    runtime = APIRuntime.from_config_path(
        str(config_path), home_root=root, data_root=root / ".openminion"
    )
    try:
        with Agent(runtime=runtime, session_id="no-browser") as agent:
            assert agent.run("hello").run_state == "completed"
    finally:
        runtime.close()

    assert constructed, "bootstrap no longer discovers the Playwright provider"
    assert not list(tmp_path.rglob("browser-playwright"))
