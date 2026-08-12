from __future__ import annotations

import pytest

from openminion.base.config.providers import ProvidersConfig
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
        "kimi",
        "zai",
        "zai-coding",
        "deepseek",
        "qwen-dashscope",
        "gemini",
        "xai",
        "mistral",
        "together",
        "custom-openai-compatible",
        "custom-anthropic-compatible",
    }.issubset(preset_by_id)
    assert preset_by_id["minimax"].runtime_adapter == "openai"
    assert preset_by_id["minimax"].api_format_id == "openai-compatible"
    assert preset_by_id["minimax"].credential_env == "MINIMAX_API_KEY"
    assert preset_by_id["minimax"].recommended_models == (
        "MiniMax-M2.7",
        "MiniMax-M2.7-highspeed",
    )
    assert preset_by_id["anthropic"].recommended_models == ("claude-sonnet-5",)
    assert preset_by_id["kimi"].default_base_url == "https://api.moonshot.ai/v1"
    assert preset_by_id["anthropic"].api_format_id == "anthropic-messages"
    assert preset_by_id["custom-anthropic-compatible"].api_format_id == (
        "anthropic-compatible"
    )
    assert preset_by_id["zai"].default_base_url == "https://api.z.ai/api/paas/v4/"
    assert (
        preset_by_id["zai-coding"].default_base_url
        == "https://api.z.ai/api/coding/paas/v4"
    )
    assert preset_by_id["deepseek"].default_base_url == "https://api.deepseek.com"
    assert preset_by_id["qwen-dashscope"].credential_env == "DASHSCOPE_API_KEY"
    assert (
        preset_by_id["gemini"].default_base_url
        == "https://generativelanguage.googleapis.com/v1beta/openai/"
    )
    assert preset_by_id["xai"].recommended_models == ("grok-4.5",)
    assert preset_by_id["mistral"].credential_env == "MISTRAL_API_KEY"
    assert preset_by_id["together"].default_base_url == "https://api.together.ai/v1"
    assert preset_by_id["custom-openai-compatible"].requires_base_url is True
    assert preset_by_id["custom-openai-compatible"].api_format_id == (
        "openai-compatible"
    )
    assert preset_by_id["ollama"].is_local is True


def test_setup_catalog_rejects_unsupported_provider_ids() -> None:
    with pytest.raises(SetupCatalogError):
        get_setup_preset("made-up-provider")


def test_primary_provider_menu_includes_local_ollama() -> None:
    first_ids = {preset.preset_id for preset in first_screen_presets()}
    more_ids = {preset.preset_id for preset in more_screen_presets()}

    assert first_ids == {"openai", "anthropic", "openrouter", "minimax", "ollama"}
    assert "ollama" not in more_ids
    assert {
        "kimi",
        "zai",
        "zai-coding",
        "deepseek",
        "qwen-dashscope",
        "gemini",
        "xai",
        "mistral",
        "together",
    }.issubset(more_ids)


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


def test_catalog_recommendation_keeps_recommended_source_when_selected() -> None:
    preset = get_setup_preset("minimax")

    result = resolve_model_choice(
        preset=preset,
        manual_model="MiniMax-M2.7-highspeed",
    )

    assert result.source == "recommended"
    assert result.models == ("MiniMax-M2.7-highspeed",)


def test_primary_provider_defaults_match_setup_recommendations() -> None:
    providers = ProvidersConfig()

    assert providers.openai.model == get_setup_preset("openai").recommended_models[0]
    assert (
        providers.anthropic.model == get_setup_preset("anthropic").recommended_models[0]
    )
    assert (
        providers.openrouter.model
        == get_setup_preset("openrouter").recommended_models[0]
    )
