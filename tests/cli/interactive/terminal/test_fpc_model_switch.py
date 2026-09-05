from __future__ import annotations

import io
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console

from openminion.api.core.profiles import (
    AgentConfigActivationError,
    RuntimeProfilesMixin,
)
from openminion.base.config import (
    AgentProfileConfig,
    ConfigManager,
    OpenMinionConfig,
    load_config,
)
from openminion.cli.interactive.runtime import OpenMinionRuntime
from openminion.cli.interactive.terminal.shell import _render_model_status


class _SessionStore:
    def __init__(self) -> None:
        self.metadata: dict[str, dict[str, str]] = {}

    def update_session_metadata(
        self,
        *,
        session_id: str,
        patch: dict[str, str],
    ) -> None:
        self.metadata.setdefault(session_id, {}).update(patch)

    def resolve_session(self, *, session_id: str, **_kwargs: Any) -> Any:
        return SimpleNamespace(id=session_id, metadata={})


class _StubAPIRuntime(RuntimeProfilesMixin):
    def __init__(self, *, config_path=None) -> None:
        self.config = OpenMinionConfig(
            agents={
                "default-agent": AgentProfileConfig(
                    name="default-agent",
                    provider="anthropic",
                    default_channel="cli",
                    provider_config_overrides={"model": "claude-sonnet-5"},
                    model_connections={
                        "anthropic": {
                            "provider": "anthropic",
                            "display_name": "Anthropic",
                            "models": ["claude-sonnet-5"],
                            "default": True,
                        },
                        "minimax": {
                            "provider": "openai",
                            "display_name": "MiniMax",
                            "models": ["MiniMax-M2.7", "MiniMax-M2.7-highspeed"],
                            "provider_config_overrides": {
                                "base_url": "https://api.minimax.io/v1",
                                "api_key_env": "MINIMAX_API_KEY",
                                "provider_identity": {
                                    "service_vendor": "minimax",
                                    "transport_adapter": "openai_chat",
                                },
                            },
                        },
                    },
                )
            },
            default_agent="default-agent",
        )
        self.config_path = config_path
        self.home_root = config_path.parent if config_path else None
        self.data_root = self.home_root / "data" if self.home_root else None
        self.config_manager = (
            ConfigManager(
                base_config=self.config,
                home_root=self.home_root,
                data_root=self.data_root,
                config_path=config_path,
            )
            if config_path
            else None
        )
        self.sessions = _SessionStore()
        self.evictions: list[tuple[str, str]] = []
        self.eviction_error: Exception | None = None
        self.gateway_overrides: list[Any] = []

    def resolve_agent_profile(
        self,
        agent_id: str | None = None,
        overrides=None,
    ) -> Any:
        return self.config.agents[agent_id or "default-agent"]

    def evict_agent_runtime(self, *, agent_id: str, reason: str) -> None:
        self.evictions.append((agent_id, reason))
        if self.eviction_error is not None:
            raise self.eviction_error

    def resolve_gateway(self, _agent_id: str, *, overrides=None) -> object:
        self.gateway_overrides.append(overrides)
        return object()


def _make_runtime(*, api_runtime: _StubAPIRuntime | None = None) -> OpenMinionRuntime:
    rt = OpenMinionRuntime.__new__(OpenMinionRuntime)
    rt._rt = api_runtime or _StubAPIRuntime()
    rt._agent_id_override = "default-agent"
    rt._agent_id = "default-agent"
    rt._channel = "cli"
    rt._target = "tui"
    rt._history_limit = 200
    rt._working_dir = ""
    rt._gateway = object()
    rt._session_id = "session-1"
    rt._conversation_id = ""
    rt._prompt_on_resume = False
    rt._project_context = None
    rt._project_context_pending = False
    rt._model_override_connection = ""
    rt._model_override_provider = ""
    rt._model_override_model = ""
    rt._action_policy_mode_override = ""
    rt._permission_mode = ""
    rt._permission_overrides = {}
    rt._read_only_mode = False
    rt._effort_level = ""
    rt._pending_candidate_session = None
    return rt


def test_list_models_returns_only_agent_configured_models() -> None:
    rows = _make_runtime().list_models()

    assert [(row.connection_id, row.model) for row in rows] == [
        ("anthropic", "claude-sonnet-5"),
        ("minimax", "MiniMax-M2.7"),
        ("minimax", "MiniMax-M2.7-highspeed"),
    ]
    assert [row.index for row in rows] == [1, 2, 3]


def test_list_models_marks_active_and_agent_default_separately() -> None:
    rows = _make_runtime().list_models()

    assert [row.index for row in rows if row.active] == [1]
    assert [row.index for row in rows if row.agent_default] == [1]


def test_switch_model_uses_configured_row_number() -> None:
    api_runtime = _StubAPIRuntime()
    rt = _make_runtime(api_runtime=api_runtime)

    selected = rt.switch_model("2")

    assert selected.connection_name == "MiniMax"
    assert rt.provider_name == "openai"
    assert rt.model_name == "MiniMax-M2.7"
    assert rt.service_vendor_name == "MiniMax"
    assert rt.transport_adapter_name == "openai_chat"
    assert api_runtime.gateway_overrides[-1].provider == "minimax"
    assert api_runtime.gateway_overrides[-1].model == "MiniMax-M2.7"


def test_switch_model_accepts_unambiguous_connection_and_model() -> None:
    rt = _make_runtime()

    selected = rt.switch_model("minimax MiniMax-M2.7-highspeed")

    assert selected.index == 3
    assert rt.model_name == "MiniMax-M2.7-highspeed"


def test_switch_model_requires_row_number_for_multi_model_connection() -> None:
    rt = _make_runtime()

    with pytest.raises(ValueError, match="multiple models"):
        rt.switch_model("minimax")


def test_switch_model_rejects_unconfigured_choice() -> None:
    rt = _make_runtime()

    with pytest.raises(ValueError, match="valid row numbers: 1, 2, 3"):
        rt.switch_model("openai/gpt-4o")


def test_switch_model_default_clears_session_override() -> None:
    api_runtime = _StubAPIRuntime()
    rt = _make_runtime(api_runtime=api_runtime)
    rt.switch_model("3")

    selected = rt.switch_model("default")

    assert selected.index == 1
    assert rt._model_override_connection == ""
    assert rt._model_override_provider == ""
    assert rt._model_override_model == ""
    assert api_runtime.gateway_overrides[-1].provider == ""
    assert api_runtime.gateway_overrides[-1].model == ""


def test_switch_model_persists_and_restores_session_selection() -> None:
    api_runtime = _StubAPIRuntime()
    first = _make_runtime(api_runtime=api_runtime)
    first.switch_model("3")
    metadata = api_runtime.sessions.metadata["session-1"]

    resumed = _make_runtime(api_runtime=api_runtime)
    resumed.restore_session_model_selection(SimpleNamespace(metadata=metadata))

    assert resumed.model_name == "MiniMax-M2.7-highspeed"
    assert resumed.service_vendor_name == "MiniMax"


def test_restore_session_without_selection_rebinds_default_gateway() -> None:
    api_runtime = _StubAPIRuntime()
    rt = _make_runtime(api_runtime=api_runtime)
    rt.switch_model("3")
    selected_gateway = rt._gateway

    rt.restore_session_model_selection(SimpleNamespace(metadata={}))

    assert rt._gateway is not selected_gateway
    assert api_runtime.gateway_overrides[-1].provider == ""
    assert api_runtime.gateway_overrides[-1].model == ""


def test_turn_metadata_uses_typed_connection_for_configured_route() -> None:
    rt = _make_runtime()
    rt.switch_model("2")

    metadata = rt._turn_inbound_metadata(None)

    assert metadata is not None
    assert metadata["override_provider"] == "minimax"
    assert metadata["override_model"] == "MiniMax-M2.7"


def test_turn_metadata_keeps_legacy_route_on_existing_profile_fields() -> None:
    api_runtime = _StubAPIRuntime()
    profile = api_runtime.config.agents["default-agent"]
    profile.model_connections = {}
    profile.provider = "openai"
    profile.provider_config_overrides = {
        "base_url": "https://api.minimax.io/v1",
        "model": "MiniMax-M2.7",
    }
    rt = _make_runtime(api_runtime=api_runtime)
    assert rt.list_models()[0].configured_connection is False
    rt.switch_model("1")

    metadata = rt._turn_inbound_metadata(None)

    assert metadata is None


def test_set_default_model_saves_agent_default(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    api_runtime = _StubAPIRuntime(config_path=config_path)
    rt = _make_runtime(api_runtime=api_runtime)

    selected = rt.set_default_model("3")
    saved = load_config(str(config_path))
    profile = saved.agents["default-agent"]

    assert selected.model == "MiniMax-M2.7-highspeed"
    assert profile.model_connections["minimax"]["default"] is True
    assert profile.provider == "openai"
    assert profile.provider_config_overrides["model"] == "MiniMax-M2.7-highspeed"
    assert api_runtime.evictions == [("default-agent", "model_default_changed")]
    assert len(api_runtime.gateway_overrides) == 1
    assert api_runtime.gateway_overrides[0].provider == ""
    assert api_runtime.gateway_overrides[0].model == ""


def test_new_session_rebinds_default_gateway() -> None:
    api_runtime = _StubAPIRuntime()
    rt = _make_runtime(api_runtime=api_runtime)
    rt.switch_model("3")
    selected_gateway = rt._gateway

    rt.create_new_session()

    assert rt._gateway is not selected_gateway
    assert api_runtime.gateway_overrides[-1].provider == ""
    assert api_runtime.gateway_overrides[-1].model == ""


def test_add_model_saves_and_selects_without_changing_agent_default(tmp_path) -> None:
    config_path = tmp_path / "agent config.json"
    rt = _make_runtime(api_runtime=_StubAPIRuntime(config_path=config_path))

    selected = rt.add_model("claude-opus-5")
    saved = load_config(str(config_path))
    profile = saved.agents["default-agent"]

    assert selected.model == "claude-opus-5"
    assert rt.model_name == "claude-opus-5"
    assert profile.model_connections["anthropic"]["models"] == [
        "claude-sonnet-5",
        "claude-opus-5",
    ]
    assert profile.provider_config_overrides["model"] == "claude-sonnet-5"
    assert profile.model_connections["anthropic"]["default"] is True


def test_add_model_materializes_legacy_connection(tmp_path) -> None:
    config_path = tmp_path / "legacy.json"
    api_runtime = _StubAPIRuntime(config_path=config_path)
    api_runtime.config.agents["default-agent"].model_connections = {}
    rt = _make_runtime(api_runtime=api_runtime)

    selected = rt.add_model("claude-opus-5")

    assert selected.connection_id == "anthropic"
    assert selected.model == "claude-opus-5"
    assert load_config(str(config_path)).agents["default-agent"].model_connections[
        "anthropic"
    ]["models"] == [
        "claude-sonnet-5",
        "claude-opus-5",
    ]


def test_model_setup_saves_refreshes_and_selects_without_restart(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    api_runtime = _StubAPIRuntime(config_path=config_path)
    api_runtime.config_manager.register(
        "config_identity",
        lambda *, base_config, home_root, data_root: base_config,
    )
    assert api_runtime.config_manager.get("config_identity") is api_runtime.config
    rt = _make_runtime(api_runtime=api_runtime)

    result = rt.build_model_setup(
        preset_id="ollama",
        model="llama3.1",
        base_url="",
        connection_id="ollama",
    )
    selected = rt.apply_model_setup(result)

    assert selected.connection_id == "ollama"
    assert selected.model == "llama3.1"
    assert api_runtime.config is api_runtime.config_manager.base_config
    assert api_runtime.config_manager.get("config_identity") is api_runtime.config
    assert load_config(str(config_path)).agents["default-agent"].model_connections[
        "ollama"
    ]["models"] == ["llama3.1"]
    assert api_runtime.gateway_overrides[-1].provider == "ollama"
    assert api_runtime.gateway_overrides[-1].model == "llama3.1"
    assert api_runtime.evictions == [("default-agent", "provider_setup_applied")]


def test_model_setup_resolves_active_runtime_environment(tmp_path) -> None:
    api_runtime = _StubAPIRuntime(config_path=tmp_path / "config.json")
    api_runtime.config.runtime.env = {"MINIMAX_API_KEY": "fixture-secret"}
    rt = _make_runtime(api_runtime=api_runtime)

    result = rt.build_model_setup(
        preset_id="minimax",
        model="MiniMax-M2.7",
        base_url="",
        connection_id="minimax-local",
    )

    assert result.preview.credential == "environment variable MINIMAX_API_KEY"
    connection = result.config.agents["default-agent"].model_connections[
        "minimax-local"
    ]
    assert connection["provider_config_overrides"]["api_key_env"] == ("MINIMAX_API_KEY")
    assert connection["provider_config_overrides"]["api_key"] == ""


def test_model_setup_rejects_result_for_different_runtime_root(tmp_path) -> None:
    api_runtime = _StubAPIRuntime(config_path=tmp_path / "config.json")
    rt = _make_runtime(api_runtime=api_runtime)
    result = rt.build_model_setup(
        preset_id="ollama",
        model="llama3.1",
        base_url="",
        connection_id="ollama",
    )

    with pytest.raises(ValueError, match="different data root"):
        api_runtime.apply_provider_setup(
            replace(result, data_root=tmp_path / "other-data"),
            agent_id="default-agent",
        )

    assert not (tmp_path / "config.json").exists()


def test_failed_model_save_does_not_mutate_running_catalog(
    tmp_path,
    monkeypatch,
) -> None:
    api_runtime = _StubAPIRuntime(config_path=tmp_path / "config.json")
    before = api_runtime.config.to_dict()

    def fail_save(*_args, **_kwargs):
        raise OSError("fixture save failure")

    monkeypatch.setattr(
        "openminion.services.bootstrap.provider_setup.atomic_save_setup_config",
        fail_save,
    )

    with pytest.raises(OSError, match="fixture save failure"):
        api_runtime.add_agent_model(
            agent_id="default-agent",
            connection_id="anthropic",
            model="claude-opus-5",
        )

    assert api_runtime.config.to_dict() == before
    assert api_runtime.config_manager.base_config is api_runtime.config
    assert api_runtime.evictions == []


def test_activation_failure_reports_saved_configuration(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    api_runtime = _StubAPIRuntime(config_path=config_path)
    api_runtime.eviction_error = RuntimeError("fixture close failure")
    rt = _make_runtime(api_runtime=api_runtime)
    result = rt.build_model_setup(
        preset_id="ollama",
        model="llama3.1",
        base_url="",
        connection_id="ollama",
    )

    with pytest.raises(AgentConfigActivationError, match="saved, but activating"):
        rt.apply_model_setup(result)

    saved = load_config(str(config_path))
    assert saved.agents["default-agent"].model_connections["ollama"]["models"] == [
        "llama3.1"
    ]


def test_render_model_status_uses_connection_model_and_api_format_columns() -> None:
    rt = _make_runtime()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=140)

    _render_model_status(runtime=rt, console=console)

    out = buf.getvalue()
    assert "Model selection" in out
    assert "agent: default-agent" in out
    assert "current model: claude-sonnet-5" in out
    assert "connection: Anthropic" in out
    assert "Connection" in out
    assert "Model" in out
    assert "API format" in out
    assert "Config key" not in out
    assert "MiniMax-M2.7-highspeed" in out
    assert "/model use <#>" in out
    assert "restored on resume" in out
    assert "save as this agent's default" in out
    assert "add to this connection and use now" in out


def test_render_model_status_marks_active_row() -> None:
    rt = _make_runtime()
    rt.switch_model("2")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=140)

    _render_model_status(runtime=rt, console=console)

    assert "◆" in buf.getvalue()
