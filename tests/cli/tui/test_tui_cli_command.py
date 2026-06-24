from __future__ import annotations

import importlib
import logging
import sys
import io
from pathlib import Path
from types import SimpleNamespace
from contextlib import redirect_stderr

import pytest

from openminion.cli.commands.tui import _silence_logging_for_tui

_MINIMAX_CONFIG_PATH = str(
    (
        Path(__file__).resolve().parents[4]
        / "test-configs"
        / "per-agent-alibaba-minimax.json"
    ).resolve(strict=False)
)
_HOME_ROOT = str(Path(__file__).resolve().parents[4])


def test_tui_command_import_is_lazy() -> None:
    sys.modules.pop("openminion.api.runtime", None)
    sys.modules.pop("openminion.cli.tui.providers.runtime", None)
    sys.modules.pop("openminion.cli.commands.tui", None)

    importlib.import_module("openminion.cli.commands.tui")

    assert "openminion.api.runtime" not in sys.modules
    assert "openminion.cli.tui.providers.runtime" not in sys.modules


def test_run_tui_demo_uses_demo_bundle(monkeypatch) -> None:
    tui_command = importlib.import_module("openminion.cli.commands.tui")
    from openminion.cli.parser import contracts as contracts_module
    from openminion.cli.tui import app as tui_app_module

    sentinel_bundle = object()
    bundle_calls: list[object] = []
    app_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        contracts_module.ProviderBundle,
        "all_demo",
        classmethod(lambda cls: bundle_calls.append(cls) or sentinel_bundle),
    )

    class _FakeApp:
        def __init__(self, runtime=None, providers=None, **kwargs) -> None:
            app_calls.append({"runtime": runtime, "providers": providers})

        def run(self) -> None:
            return None

    monkeypatch.setattr(tui_app_module, "OpenMinionApp", _FakeApp)

    args = SimpleNamespace(
        demo=True,
        agent="agent-02",
        config=None,
        home_root=None,
        data_root=None,
    )
    assert tui_command.run_tui(args) == 0
    assert bundle_calls
    assert app_calls and app_calls[0]["providers"] is sentinel_bundle


def test_run_tui_live_wires_runtime_bundle_and_closes(monkeypatch) -> None:
    tui_command = importlib.import_module("openminion.cli.commands.tui")
    from openminion.cli.parser import contracts as contracts_module
    from openminion.cli.tui import app as tui_app_module
    from openminion.cli.tui import providers as providers_module
    from openminion.api.runtime import APIRuntime

    sentinel_bundle = object()
    captured_runtime: dict[str, object] = {}
    sync_calls: list[dict[str, object]] = []

    real_from_config_path = APIRuntime.from_config_path

    def _capture_runtime(config_path, *, home_root=None, data_root=None):
        runtime = real_from_config_path(
            config_path,
            home_root=home_root,
            data_root=data_root,
        )
        captured_runtime["runtime"] = runtime
        return runtime

    class _FakeTuiRuntime:
        def __init__(self, _runtime, **kwargs) -> None:
            # Accept (and ignore) additive shared-adapter kwargs like
            # `prompt_on_resume` so Phase 7's opt-in from `commands/tui.py`
            # does not break this test fake.
            del kwargs
            self.agent_id = "default-agent"
            self.switch_calls: list[str] = []

        def switch_agent(self, agent_id: str) -> None:
            self.agent_id = agent_id
            self.switch_calls.append(agent_id)

    app_calls: list[dict[str, object]] = []

    class _FakeApp:
        def __init__(self, runtime=None, providers=None, **kwargs) -> None:
            app_calls.append(
                {"runtime": runtime, "providers": providers, "kwargs": dict(kwargs)}
            )

        def run(self) -> None:
            return None

    monkeypatch.setattr(
        APIRuntime,
        "from_config_path",
        staticmethod(_capture_runtime),
    )
    monkeypatch.setattr(
        tui_command,
        "sync_cli_identity_profiles",
        lambda **kwargs: sync_calls.append(dict(kwargs)),
    )
    monkeypatch.setattr(providers_module, "OpenMinionRuntime", _FakeTuiRuntime)
    monkeypatch.setattr(
        contracts_module.ProviderBundle,
        "from_api_runtime",
        classmethod(lambda cls, rt: sentinel_bundle),
    )
    monkeypatch.setattr(tui_app_module, "OpenMinionApp", _FakeApp)

    args = SimpleNamespace(
        demo=False,
        agent="ops-agent",
        config=_MINIMAX_CONFIG_PATH,
        home_root=_HOME_ROOT,
        data_root=None,
        sync_identity=True,
    )
    assert tui_command.run_tui(args) == 0
    assert len(sync_calls) == 1
    assert sync_calls[0]["enabled"] is True
    assert app_calls and app_calls[0]["providers"] is sentinel_bundle
    assert app_calls[0]["kwargs"]["initial_tab"] == "tab-agents"
    fake_tui_runtime = app_calls[0]["runtime"]
    assert getattr(fake_tui_runtime, "switch_calls", []) == []
    runtime_obj = captured_runtime.get("runtime")
    assert runtime_obj is not None
    assert getattr(runtime_obj, "_closed", False) is True


def test_run_tui_missing_config_interactive_launches_inline_setup(monkeypatch) -> None:
    tui_command = importlib.import_module("openminion.cli.commands.tui")
    from openminion.cli.parser import contracts as contracts_module
    from openminion.cli.tui import app as tui_app_module
    from openminion.cli.tui import providers as providers_module
    from openminion.api.runtime import APIRuntime
    from openminion.services.bootstrap.onboarding import (
        OnboardingAction,
        OnboardingState,
        OnboardingStatus,
        OnboardingTrack,
    )

    sentinel_bundle = object()
    app_calls: list[dict[str, object]] = []
    captured_runtime: dict[str, object] = {}

    class _FakeApp:
        def __init__(self, runtime=None, providers=None, **kwargs) -> None:
            app_calls.append({"runtime": runtime, "providers": providers})

        def run(self) -> None:
            return None

    class _FakeTuiRuntime:
        def __init__(self, _runtime, **kwargs) -> None:
            del kwargs
            self.agent_id = "default-agent"

        def switch_agent(self, agent_id: str) -> None:
            self.agent_id = agent_id

    real_from_config_path = APIRuntime.from_config_path

    def _capture_runtime(config_path, *, home_root=None, data_root=None):
        runtime = real_from_config_path(
            config_path,
            home_root=home_root,
            data_root=data_root,
        )
        captured_runtime["runtime"] = runtime
        return runtime

    monkeypatch.setattr(
        tui_command,
        "_inspect_tui_onboarding",
        lambda args: OnboardingStatus(
            state=OnboardingState.MISSING_CONFIG,
            action=OnboardingAction.LAUNCH_SETUP,
            track=OnboardingTrack.UNKNOWN,
            reason="missing config",
            config_path=None,  # type: ignore[arg-type]
            home_root=None,  # type: ignore[arg-type]
            data_root=None,  # type: ignore[arg-type]
        ),
    )
    monkeypatch.setattr(tui_command, "_run_inline_setup_for_tui", lambda args: 0)
    monkeypatch.setattr(
        APIRuntime,
        "from_config_path",
        staticmethod(_capture_runtime),
    )
    monkeypatch.setattr(providers_module, "OpenMinionRuntime", _FakeTuiRuntime)
    monkeypatch.setattr(
        contracts_module.ProviderBundle,
        "from_api_runtime",
        classmethod(lambda cls, rt: sentinel_bundle),
    )
    monkeypatch.setattr(tui_app_module, "OpenMinionApp", _FakeApp)

    args = SimpleNamespace(
        demo=False,
        agent="ops-agent",
        config=_MINIMAX_CONFIG_PATH,
        home_root=_HOME_ROOT,
        data_root=None,
        no_picker=False,
        no_interactive=False,
        sync_identity=False,
        theme=None,
    )
    assert tui_command.run_tui(args) == 0
    assert app_calls and app_calls[0]["providers"] is sentinel_bundle
    runtime_obj = captured_runtime.get("runtime")
    assert runtime_obj is not None
    assert getattr(runtime_obj, "_closed", False) is True


def test_run_tui_missing_config_noninteractive_fails_fast(monkeypatch) -> None:
    tui_command = importlib.import_module("openminion.cli.commands.tui")
    from openminion.services.bootstrap.onboarding import (
        OnboardingAction,
        OnboardingState,
        OnboardingStatus,
        OnboardingTrack,
    )

    monkeypatch.setattr(
        tui_command,
        "_inspect_tui_onboarding",
        lambda args: OnboardingStatus(
            state=OnboardingState.MISSING_CONFIG,
            action=OnboardingAction.FAIL_FAST,
            track=OnboardingTrack.UNKNOWN,
            reason="no config found",
            config_path=None,  # type: ignore[arg-type]
            home_root=None,  # type: ignore[arg-type]
            data_root=None,  # type: ignore[arg-type]
        ),
    )
    args = SimpleNamespace(
        demo=False,
        agent=None,
        config=None,
        home_root=None,
        data_root=None,
        no_picker=False,
    )
    buf = io.StringIO()
    with redirect_stderr(buf):
        assert tui_command.run_tui(args) == 2
    output = buf.getvalue()
    assert "openminion tui: error" in output
    assert "run: openminion setup" in output


def test_run_tui_missing_config_interactive_uses_inline_setup(monkeypatch) -> None:
    tui_command = importlib.import_module("openminion.cli.commands.tui")
    from openminion.services.bootstrap.onboarding import (
        OnboardingAction,
        OnboardingState,
        OnboardingStatus,
        OnboardingTrack,
    )

    monkeypatch.setattr(
        tui_command,
        "_inspect_tui_onboarding",
        lambda args: OnboardingStatus(
            state=OnboardingState.MISSING_CONFIG,
            action=OnboardingAction.LAUNCH_SETUP,
            track=OnboardingTrack.UNKNOWN,
            reason="missing config",
            config_path=None,  # type: ignore[arg-type]
            home_root=None,  # type: ignore[arg-type]
            data_root=None,  # type: ignore[arg-type]
        ),
    )
    setup_calls: list[object] = []
    monkeypatch.setattr(
        tui_command,
        "_run_inline_setup_for_tui",
        lambda args: setup_calls.append(args) or 0,
    )
    monkeypatch.setattr(tui_command, "_silence_logging_for_tui", lambda args: None)

    class _FakeRuntime(SimpleNamespace):
        def close(self) -> None:
            return None

    class _FakeTuiRuntime:
        def __init__(self, runtime, **kwargs) -> None:
            self.agent_id = "default-agent"

        def switch_agent(self, agent_id: str) -> None:
            self.agent_id = agent_id

    class _FakeBundle:
        @classmethod
        def from_api_runtime(cls, runtime):
            return cls()

    class _FakeApp:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self) -> None:
            return None

    monkeypatch.setattr(
        "openminion.api.runtime.APIRuntime.from_config_path",
        staticmethod(lambda *args, **kwargs: _FakeRuntime()),
    )
    monkeypatch.setattr(
        "openminion.cli.tui.providers.OpenMinionRuntime", _FakeTuiRuntime
    )
    monkeypatch.setattr("openminion.cli.parser.contracts.ProviderBundle", _FakeBundle)
    monkeypatch.setattr("openminion.cli.tui.app.OpenMinionApp", _FakeApp)

    args = SimpleNamespace(
        demo=False,
        agent=None,
        config=_MINIMAX_CONFIG_PATH,
        home_root=None,
        data_root=None,
        no_picker=False,
        no_interactive=False,
        sync_identity=False,
        theme=None,
    )
    assert tui_command.run_tui(args) == 0
    assert len(setup_calls) == 1


def test_run_tui_explicit_missing_config_omits_setup_remediation(monkeypatch) -> None:
    tui_command = importlib.import_module("openminion.cli.commands.tui")
    from openminion.services.bootstrap.onboarding import (
        OnboardingAction,
        OnboardingState,
        OnboardingStatus,
        OnboardingTrack,
    )

    monkeypatch.setattr(
        tui_command,
        "_inspect_tui_onboarding",
        lambda args: OnboardingStatus(
            state=OnboardingState.MISSING_CONFIG,
            action=OnboardingAction.FAIL_FAST,
            track=OnboardingTrack.UNKNOWN,
            reason="Config file does not exist: /tmp/missing.json",
            config_path=Path("/tmp/missing.json"),
            home_root=Path("/tmp"),
            data_root=Path("/tmp/.openminion"),
            remediation_command="",
        ),
    )
    args = SimpleNamespace(
        demo=False,
        agent=None,
        config="/tmp/missing.json",
        home_root=None,
        data_root=None,
        no_picker=False,
    )
    buf = io.StringIO()
    with redirect_stderr(buf):
        assert tui_command.run_tui(args) == 2
    output = buf.getvalue()
    assert "Config file does not exist: /tmp/missing.json" in output
    assert "run:" not in output


def test_silence_logging_for_tui_uses_resolved_cli_roots(monkeypatch, tmp_path) -> None:
    state_root = tmp_path / "runtime-data"

    original_handlers = list(logging.getLogger().handlers)
    try:
        log_path = _silence_logging_for_tui(
            SimpleNamespace(
                config=None,
                home_root=str(tmp_path),
                data_root=str(state_root),
            )
        )
    finally:
        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()
        for handler in original_handlers:
            root.addHandler(handler)

    assert log_path == str((state_root / "logs" / "tui.log").resolve(strict=False))


@pytest.mark.asyncio
async def test_onboarding_wizard_flow_saves_config_and_exits(
    monkeypatch, tmp_path
) -> None:
    from openminion.cli.tui.app import OpenMinionApp

    config_path = tmp_path / "agent.json"
    captured: dict[str, object] = {}

    def _fake_save_config(config, path, *, home_root=None):
        captured["config"] = config
        captured["path"] = path
        captured["home_root"] = home_root
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("{}")
        return Path(path)

    monkeypatch.setattr("openminion.base.config.save_config", _fake_save_config)

    app = OpenMinionApp(
        onboarding_request={
            "config_path": str(config_path),
            "home_root": str(tmp_path),
            "data_root": str(tmp_path / ".openminion"),
            "agent_id": "seed-agent",
        }
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.__class__.__name__ == "OnboardingWizardScreen"

        app.screen.query_one("#onboarding-config-path").value = str(config_path)
        app.screen.query_one("#onboarding-next").press()
        await pilot.pause()

        app.screen.query_one("#onboarding-provider").value = "openai"
        app.screen.query_one("#onboarding-model").value = "gpt-5.4"
        app.screen.query_one("#onboarding-next").press()
        await pilot.pause()

        app.screen.query_one("#onboarding-agent-id").value = "builder"
        app.screen.query_one("#onboarding-next").press()
        await pilot.pause()

    assert app.return_value == str(config_path)
    saved = captured["config"]
    default_agent_id = str(getattr(saved, "default_agent", "") or "").strip()
    agents = getattr(saved, "agents", {})
    effective_agent_id = default_agent_id or "builder"
    default_profile = agents.get(effective_agent_id)
    assert getattr(default_profile, "name", "") == "builder"
    assert getattr(default_profile, "provider", "") == "openai"
    assert getattr(saved.providers.openai, "model", "") == "gpt-5.4"
    assert "builder" in agents
