from __future__ import annotations

import pytest

from openminion.modules.llm.setup_catalog import (
    SetupCatalogError,
    first_screen_presets,
    get_setup_preset,
    list_setup_presets,
    more_screen_presets,
    resolve_model_choice,
)


def test_setup_catalog_covers_required_provider_matrix() -> None:
    preset_by_id = {preset.preset_id: preset for preset in list_setup_presets()}

    assert {
        "openai",
        "anthropic",
        "openrouter",
        "cerebras",
        "groq",
        "ollama",
        "cortensor",
        "minimax",
        "custom-openai-compatible",
        "custom-anthropic-compatible",
    }.issubset(preset_by_id)
    assert preset_by_id["minimax"].runtime_adapter == "openai"
    assert preset_by_id["minimax"].credential_env == "MINIMAX_API_KEY"
    assert preset_by_id["custom-openai-compatible"].requires_base_url is True
    assert preset_by_id["ollama"].is_local is True


def test_setup_catalog_rejects_unsupported_provider_ids() -> None:
    with pytest.raises(SetupCatalogError):
        get_setup_preset("made-up-provider")


def test_hosted_provider_menus_exclude_local_ollama() -> None:
    first_ids = {preset.preset_id for preset in first_screen_presets()}
    more_ids = {preset.preset_id for preset in more_screen_presets()}

    assert first_ids == {"openai", "anthropic", "openrouter"}
    assert "ollama" not in more_ids


def test_model_choice_records_live_source_only_for_endpoint_models() -> None:
    preset = get_setup_preset("openai")

    result = resolve_model_choice(
        preset=preset,
        list_models=lambda _preset: ["gpt-live-a", "gpt-live-b"],
    )

    assert result.source == "live"
    assert result.endpoint_verified is True
    assert result.models == ("gpt-live-a", "gpt-live-b")


def test_model_choice_falls_back_truthfully_after_discovery_failure() -> None:
    preset = get_setup_preset("openai")

    result = resolve_model_choice(
        preset=preset,
        configured_model="gpt-configured",
        list_models=lambda _preset: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    assert result.source == "configured"
    assert result.models == ("gpt-configured",)
    assert "offline" in result.warning


def test_model_choice_manual_source_wins_without_ranking() -> None:
    preset = get_setup_preset("openai")

    result = resolve_model_choice(
        preset=preset,
        configured_model="gpt-configured",
        manual_model="operator-model",
        list_models=lambda _preset: ["gpt-live"],
    )

    assert result.source == "manual"
    assert result.models == ("operator-model",)
