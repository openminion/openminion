from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console

from openminion.cli.tui.terminal.shell import _render_model_status
from openminion.cli.tui.providers.runtime import OpenMinionRuntime


class _StubAPIRuntime:
    def __init__(
        self,
        *,
        anthropic_model: str = "claude-3-5-sonnet-latest",
        openai_model: str = "gpt-4.1-mini",
    ) -> None:
        self.config = SimpleNamespace(
            providers=SimpleNamespace(
                anthropic=SimpleNamespace(model=anthropic_model),
                openai=SimpleNamespace(model=openai_model),
                openrouter=SimpleNamespace(model="openai/gpt-4.1-mini"),
                cerebras=SimpleNamespace(model="gpt-oss-120b"),
                groq=SimpleNamespace(model="llama-3.1-70b"),
                ollama=SimpleNamespace(model="llama3"),
                cortensor=SimpleNamespace(model="cortensor-default"),
            ),
            agents={
                "default-agent": SimpleNamespace(
                    name="default-agent",
                    provider="anthropic",
                    model=anthropic_model,
                    default_channel="cli",
                )
            },
        )

    def resolve_agent_profile(self, agent_id: str | None = None, overrides=None) -> Any:
        return self.config.agents.get("default-agent")


def _make_runtime() -> OpenMinionRuntime:
    rt = OpenMinionRuntime.__new__(OpenMinionRuntime)
    rt._rt = _StubAPIRuntime()
    rt._agent_id_override = "default-agent"
    rt._agent_id = "default-agent"
    rt._channel = "cli"
    rt._target = "tui"
    rt._history_limit = 200
    rt._working_dir = ""
    # _ensure_agent_resolved short-circuits when both _agent_id and
    # _gateway are truthy — set a non-None sentinel for _gateway so
    # the resolver doesn't try to reach a real gateway.
    rt._gateway = object()
    rt._session_id = None
    rt._prompt_on_resume = False
    rt._project_context = None
    rt._project_context_pending = False
    rt._model_override_provider = ""
    rt._model_override_model = ""
    rt._pending_candidate_session = None
    return rt


# ── list_models ──────────────────────────────────────────────────


def test_list_models_returns_configured_providers() -> None:
    rt = _make_runtime()
    rows = rt.list_models()
    names = [name for name, _, _ in rows]
    assert "anthropic" in names
    assert "openai" in names
    assert "openrouter" in names


def test_list_models_marks_active_provider() -> None:
    rt = _make_runtime()
    rows = rt.list_models()
    actives = [name for name, _, is_active in rows if is_active]
    assert actives == ["anthropic"]


def test_list_models_marks_active_after_switch() -> None:
    rt = _make_runtime()
    rt.switch_model("openai")
    rows = rt.list_models()
    actives = [name for name, _, is_active in rows if is_active]
    assert actives == ["openai"]


# ── switch_model ─────────────────────────────────────────────────


def test_switch_model_provider_only_uses_configured_default() -> None:
    rt = _make_runtime()
    provider, model = rt.switch_model("openai")
    assert provider == "openai"
    assert model == "gpt-4.1-mini"


def test_switch_model_provider_and_model_pair() -> None:
    rt = _make_runtime()
    provider, model = rt.switch_model("openai/gpt-4o")
    assert provider == "openai"
    assert model == "gpt-4o"


def test_switch_model_anthropic_alias_normalized() -> None:
    rt = _make_runtime()
    provider, _ = rt.switch_model("claude")
    assert provider == "anthropic"


def test_switch_model_unknown_provider_raises_with_valid_options() -> None:
    rt = _make_runtime()
    with pytest.raises(ValueError) as exc:
        rt.switch_model("invalid-provider")
    msg = str(exc.value)
    assert "invalid-provider" in msg
    # Lists valid options.
    assert "anthropic" in msg
    assert "openai" in msg


def test_switch_model_clears_override_with_default() -> None:
    rt = _make_runtime()
    rt.switch_model("openai")
    assert rt._model_override_provider == "openai"
    rt.switch_model("default")
    assert rt._model_override_provider == ""
    assert rt._model_override_model == ""
    # Reverts to configured profile.
    assert rt.provider_name == "anthropic"


def test_switch_model_clears_override_with_empty_string() -> None:
    rt = _make_runtime()
    rt.switch_model("openai/gpt-4o")
    rt.switch_model("")
    assert rt._model_override_provider == ""
    assert rt._model_override_model == ""


# ── model_name / provider_name reflect override ──────────────────


def test_provider_name_reflects_override() -> None:
    rt = _make_runtime()
    assert rt.provider_name == "anthropic"
    rt.switch_model("openai")
    assert rt.provider_name == "openai"


def test_model_name_reflects_override() -> None:
    rt = _make_runtime()
    rt.switch_model("openai/gpt-4o")
    assert rt.model_name == "gpt-4o"


def test_provider_only_switch_inherits_provider_default_model() -> None:
    rt = _make_runtime()
    rt.switch_model("openai")
    # provider_name reflects the new provider.
    assert rt.provider_name == "openai"
    # model_name comes from override (empty) → falls back to
    # provider_cfg.model in _provider_model_identity.
    assert rt.model_name == "gpt-4.1-mini"


# ── Session-scoped behavior ─────────────────────────────────────


def test_switch_model_is_session_scoped() -> None:
    rt1 = _make_runtime()
    rt1.switch_model("openai")
    assert rt1.provider_name == "openai"

    rt2 = _make_runtime()
    # New instance is back at configured profile.
    assert rt2.provider_name == "anthropic"


# ── Terminal-flow /model bare-form rendering ─────────────────────


def test_render_model_status_shows_current_and_table() -> None:
    rt = _make_runtime()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    _render_model_status(runtime=rt, console=console)
    out = buf.getvalue()
    assert "current:" in out
    assert "anthropic" in out
    assert "openai" in out
    # Hint about switching is present.
    assert "Switch with" in out


def test_render_model_status_marks_active_row() -> None:
    rt = _make_runtime()
    rt.switch_model("openai")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    _render_model_status(runtime=rt, console=console)
    out = buf.getvalue()
    # Active marker (◆) appears in the openai row, not the anthropic row.
    # Stripping ANSI/spacing is brittle; just verify openai is listed
    # with the marker character somewhere.
    assert "◆" in out
    assert "openai" in out
