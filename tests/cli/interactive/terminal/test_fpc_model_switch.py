from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console

from openminion.api.core.profiles import RuntimeProfilesMixin
from openminion.base.config import (
    AgentProfileConfig,
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
        self.sessions = _SessionStore()
        self.evictions: list[tuple[str, str]] = []

    def resolve_agent_profile(
        self,
        agent_id: str | None = None,
        overrides=None,
    ) -> Any:
        return self.config.agents[agent_id or "default-agent"]

    def evict_agent_runtime(self, *, agent_id: str, reason: str) -> None:
        self.evictions.append((agent_id, reason))

    def resolve_gateway(self, _agent_id: str) -> object:
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
    rt = _make_runtime()

    selected = rt.switch_model("2")

    assert selected.connection_name == "MiniMax"
    assert rt.provider_name == "openai"
    assert rt.model_name == "MiniMax-M2.7"
    assert rt.service_vendor_name == "MiniMax"
    assert rt.transport_adapter_name == "openai_chat"


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
    rt = _make_runtime()
    rt.switch_model("3")

    selected = rt.switch_model("default")

    assert selected.index == 1
    assert rt._model_override_connection == ""
    assert rt._model_override_provider == ""
    assert rt._model_override_model == ""


def test_switch_model_persists_and_restores_session_selection() -> None:
    api_runtime = _StubAPIRuntime()
    first = _make_runtime(api_runtime=api_runtime)
    first.switch_model("3")
    metadata = api_runtime.sessions.metadata["session-1"]

    resumed = _make_runtime(api_runtime=api_runtime)
    resumed.restore_session_model_selection(SimpleNamespace(metadata=metadata))

    assert resumed.model_name == "MiniMax-M2.7-highspeed"
    assert resumed.service_vendor_name == "MiniMax"


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


def test_model_setup_command_targets_active_agent_and_config(tmp_path) -> None:
    config_path = tmp_path / "agent config.json"
    rt = _make_runtime(api_runtime=_StubAPIRuntime(config_path=config_path))

    command = rt.model_setup_command()

    assert "setup --add-model --no-focus" in command
    assert "--agent default-agent" in command
    assert str(config_path) in command


def test_render_model_status_uses_connection_model_and_api_format_columns() -> None:
    rt = _make_runtime()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=140)

    _render_model_status(runtime=rt, console=console)

    out = buf.getvalue()
    assert "agent: default-agent" in out
    assert "current model: claude-sonnet-5" in out
    assert "connection: Anthropic" in out
    assert "Connection" in out
    assert "Model" in out
    assert "API format" in out
    assert "Config key" not in out
    assert "MiniMax-M2.7-highspeed" in out
    assert "/model use <#>" in out


def test_render_model_status_marks_active_row() -> None:
    rt = _make_runtime()
    rt.switch_model("2")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=140)

    _render_model_status(runtime=rt, console=console)

    assert "◆" in buf.getvalue()
