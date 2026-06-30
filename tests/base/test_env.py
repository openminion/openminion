from __future__ import annotations

from openminion.base.config import (
    EnvironmentConfig,
    OpenMinionConfig,
    validate_for_provider,
    validate_runtime_core,
)
from tests._csc_fixtures import _csc_install_default_agent


def test_environment_config_source_precedence() -> None:
    env = EnvironmentConfig.from_sources(
        process_env={"OPENAI_API_KEY": "process", "OPENMINION_TRACE_REQUESTS": "1"},
        runtime_env={"OPENAI_API_KEY": "runtime", "OPENMINION_HOME": "/tmp/home"},
    )
    assert env.openai_api_key == "process"
    assert env.openminion_home == "/tmp/home"
    assert env.openminion_trace_requests is True


def test_environment_config_typed_getters() -> None:
    env = EnvironmentConfig.from_sources(
        process_env={
            "BOOL_TRUE": "yes",
            "COUNT": "5",
            "RATIO": "0.75",
            "LIST": "a, b, ,c",
        }
    )
    assert env.get_bool("BOOL_TRUE", False) is True
    assert env.get_int("COUNT", 0) == 5
    assert env.get_float("RATIO", 0.0) == 0.75
    assert env.get_list("LIST") == ["a", "b", "c"]
    assert env.get_list("MISSING", default=["x"]) == ["x"]


def test_environment_config_curated_properties() -> None:
    env = EnvironmentConfig.from_sources(
        process_env={
            "OPENMINION_HOME": "/workspace",
            "OPENMINION_DATA_ROOT": "/workspace/.openminion",
            "OPENMINION_DATA_ROOT_ENFORCEMENT": "warn",
            "OPENMINION_LLM_DEBUG_MAX_CHARS": "1024",
            "OPENMINION_SHOW_RESPONSE_TIME": "0",
            "OPENMINION_TURN_TIMEOUT_SECONDS": "45",
            "BRAVE_API_KEY": "brave-key",
            "TAVILY_API_KEY": "tavily-key",
        }
    )
    assert env.openminion_home == "/workspace"
    assert env.openminion_data_root == "/workspace/.openminion"
    assert env.openminion_data_root_enforcement == "soft"
    assert env.openminion_llm_debug_max_chars == 1024
    assert env.openminion_show_response_time is False
    assert env.openminion_turn_timeout_seconds == 45
    assert env.brave_api_key == "brave-key"
    assert env.tavily_api_key == "tavily-key"


def test_environment_config_shows_response_time_by_default() -> None:
    env = EnvironmentConfig.from_sources(process_env={})
    assert env.openminion_show_response_time is True


def test_validate_for_provider_reports_missing_required_key() -> None:
    env = EnvironmentConfig.from_sources(process_env={})
    result = validate_for_provider(provider_name="openai", env=env)
    assert result.ok is False
    assert "OPENAI_API_KEY" in result.required_vars
    assert result.errors


def test_validate_for_provider_respects_config_api_key() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.providers.openai.api_key = "from-config"
    env = EnvironmentConfig.from_sources(process_env={})
    result = validate_for_provider(provider_name="openai", env=env, config=config)
    assert result.ok is True
    assert result.required_vars == ()
    assert result.errors == ()


def test_validate_for_provider_supports_claude_alias() -> None:
    env = EnvironmentConfig.from_sources(process_env={})
    result = validate_for_provider(provider_name="claude", env=env)
    assert result.ok is False
    assert result.required_vars == ("ANTHROPIC_API_KEY",)


def test_validate_for_provider_uses_custom_provider_env_name() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.providers.openrouter.api_key_env = "CUSTOM_OPENROUTER_KEY"
    env = EnvironmentConfig.from_sources(process_env={})
    result = validate_for_provider(provider_name="openrouter", env=env, config=config)
    assert result.ok is False
    assert result.required_vars == ("CUSTOM_OPENROUTER_KEY",)
    assert result.errors == (
        "openrouter provider requires env var CUSTOM_OPENROUTER_KEY (or provider api_key in config).",
    )


def test_validate_runtime_core_emits_deprecation_warnings() -> None:
    env = EnvironmentConfig.from_sources(
        process_env={
            "OPENMINION_CONFIG_ROOT": "/tmp/old",
            "OPENMINION_LOG_COLOR": "1",
            "OPENMINION_DATA_ROOT_ENFORCEMENT": "legacy",
        }
    )
    result = validate_runtime_core(env)
    assert result.ok is True
    assert any(
        "OPENMINION_CONFIG_ROOT is deprecated" in warning for warning in result.warnings
    )
    assert any(
        "OPENMINION_LOG_COLOR is deprecated" in warning for warning in result.warnings
    )
    assert any(
        "OPENMINION_DATA_ROOT_ENFORCEMENT should be one of" in warning
        for warning in result.warnings
    )
