from __future__ import annotations

from openminion.base.config import OpenMinionConfig
from openminion.modules.llm.config import (
    LLMCTLConfig,
    ProviderConfig,
    from_base_config,
    resolve_provider_config,
)
from openminion.modules.llm.providers.cortensor.config import (
    resolve_cortensor_runtime_config,
)
from tests._csc_fixtures import _csc_install_default_agent


def test_resolve_provider_config_uses_injected_env_mapping() -> None:
    config = LLMCTLConfig(
        providers={
            "openai": ProviderConfig(api_key_env="OPENAI_API_KEY"),
        }
    )

    resolved = resolve_provider_config(
        config,
        "openai",
        env={"OPENAI_API_KEY": "injected-key"},
    )

    assert resolved["api_key"] == "injected-key"


def test_resolve_provider_config_keeps_config_key_when_env_unset() -> None:
    config = LLMCTLConfig(
        providers={
            "openai": ProviderConfig(
                api_key_env="OPENMINION_TEST_OPENAI_MISSING",
                api_key="configured-key",
            ),
        }
    )

    resolved = resolve_provider_config(
        config,
        "openai",
        env={},
    )

    assert resolved["api_key"] == "configured-key"


def test_resolve_provider_config_keeps_config_key_when_env_is_stale() -> None:
    config = LLMCTLConfig(
        providers={
            "openai": ProviderConfig(
                api_key_env="OPENMINION_TEST_OPENAI_STALE",
                api_key="configured-key",
            ),
        }
    )

    resolved = resolve_provider_config(
        config,
        "openai",
        env={"OPENMINION_TEST_OPENAI_STALE": "stale-process-key"},
    )

    assert resolved["api_key"] == "configured-key"


def test_resolve_provider_config_preserves_provider_identity_payload() -> None:
    config = LLMCTLConfig(
        providers={
            "openai": ProviderConfig(
                provider_identity={
                    "transport_adapter": "openai_chat",
                    "wire_protocol_family": "openai_chat_completions",
                    "service_vendor": "minimax",
                    "model_family": "minimax",
                }
            ),
        }
    )

    resolved = resolve_provider_config(config, "openai", env={})

    assert resolved["provider_identity"] == {
        "transport_adapter": "openai_chat",
        "wire_protocol_family": "openai_chat_completions",
        "service_vendor": "minimax",
        "model_family": "minimax",
    }


def test_resolve_provider_config_translates_openai_minimax_legacy_identity() -> None:
    config = LLMCTLConfig(
        providers={
            "openai": ProviderConfig(
                model="MiniMax-M2.7",
                base_url="https://api.minimax.io/v1",
            ),
        }
    )

    resolved = resolve_provider_config(config, "openai", env={})

    assert resolved["provider_identity"] == {
        "transport_adapter": "openai_chat",
        "wire_protocol_family": "openai_chat_completions",
        "service_vendor": "minimax",
        "model_family": "minimax",
    }


def test_llm_from_base_config_uses_runtime_env_for_provider_key(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENMINION_TEST_MINIMAX_KEY", raising=False)
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="openai")  # type: ignore[attr-defined]
    config.providers.openai.api_key = ""
    config.providers.openai.api_key_env = "OPENMINION_TEST_MINIMAX_KEY"
    config.runtime.env = {"OPENMINION_TEST_MINIMAX_KEY": "runtime-key"}

    llm_config = from_base_config(
        base_config=config,
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
    )
    resolved = resolve_provider_config(llm_config, "openai", env={})

    assert resolved["api_key"] == "runtime-key"


def test_llm_from_base_config_prefers_process_env_over_runtime_env(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENMINION_TEST_MINIMAX_KEY", "process-key")
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="openai")  # type: ignore[attr-defined]
    config.providers.openai.api_key = ""
    config.providers.openai.api_key_env = "OPENMINION_TEST_MINIMAX_KEY"
    config.runtime.env = {"OPENMINION_TEST_MINIMAX_KEY": "runtime-key"}

    llm_config = from_base_config(
        base_config=config,
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
    )
    resolved = resolve_provider_config(llm_config, "openai", env={})

    assert resolved["api_key"] == "process-key"


def test_resolve_cortensor_runtime_config_uses_injected_env_mapping() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.providers.cortensor.base_url = "https://default.example"
    config.providers.cortensor.session_parallel_requests = 1

    resolved = resolve_cortensor_runtime_config(
        config,
        env={
            "CORTENSOR_API_URL": "https://override.example",
            "CORTENSOR_SESSION_PARALLEL_REQUESTS": "4",
        },
    )

    assert resolved.base_url == "https://override.example"
    assert int(resolved.session_parallel_requests) == 4
