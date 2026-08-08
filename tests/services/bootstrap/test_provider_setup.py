from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from openminion.base.config import AgentProfileConfig, OpenMinionConfig
from openminion.base.config.runtime.profile import build_runtime_config
from openminion.services.bootstrap.provider_setup import (
    ProviderSetupError,
    ProviderSetupRequest,
    atomic_save_setup_config,
    build_provider_setup,
    redacted_config_payload,
    resolve_setup_credential,
)
from openminion.modules.llm.setup_catalog import get_setup_preset


def test_env_first_credentials_store_reference_not_secret(tmp_path: Path) -> None:
    result = build_provider_setup(
        ProviderSetupRequest(
            preset_id="openai",
            agent_id="ops",
            model="gpt-4.1-mini",
            config_path=str(tmp_path / ".openminion" / "agents.json"),
            home_root=tmp_path,
            data_root=tmp_path / ".openminion",
            env={"OPENAI_API_KEY": "sk-live"},
        )
    )

    payload = result.config.to_dict()
    assert payload["providers"]["openai"]["api_key_env"] == "OPENAI_API_KEY"
    assert payload["providers"]["openai"]["api_key"] == ""
    assert result.preview.credential == "environment variable OPENAI_API_KEY"


def test_env_first_setup_clears_stale_stored_provider_secret(tmp_path: Path) -> None:
    existing = OpenMinionConfig()
    existing.agents = {"ops": AgentProfileConfig(name="ops", provider="openai")}
    existing.default_agent = "ops"
    existing.providers.openai.api_key = "old-stored-secret"
    existing.providers.openai.api_key_env = "OLD_OPENAI_KEY"

    result = build_provider_setup(
        ProviderSetupRequest(
            preset_id="openai",
            agent_id="ops",
            model="gpt-4.1-mini",
            config_path=str(tmp_path / ".openminion" / "agents.json"),
            home_root=tmp_path,
            data_root=tmp_path / ".openminion",
            env={"OPENAI_API_KEY": "sk-live"},
        ),
        existing_config=existing,
    )

    payload = result.config.to_dict()
    assert payload["providers"]["openai"]["api_key"] == ""
    assert payload["providers"]["openai"]["api_key_env"] == "OPENAI_API_KEY"


def test_setup_replaces_stale_selected_agent_provider_overrides(
    tmp_path: Path,
) -> None:
    existing = OpenMinionConfig()
    existing.agents = {
        "ops": AgentProfileConfig(
            name="ops",
            provider="openai",
            provider_config_overrides={
                "api_key": "old-agent-secret",
                "api_key_env": "OLD_AGENT_KEY",
                "base_url": "https://old.example.invalid/v1",
                "model": "old-model",
                "temperature": 0.4,
            },
        )
    }
    existing.default_agent = "ops"

    result = build_provider_setup(
        ProviderSetupRequest(
            preset_id="openai",
            agent_id="ops",
            model="gpt-4.1-mini",
            base_url="https://api.openai.com/v1",
            config_path=str(tmp_path / ".openminion" / "agents.json"),
            home_root=tmp_path,
            data_root=tmp_path / ".openminion",
            env={"OPENAI_API_KEY": "sk-live"},
        ),
        existing_config=existing,
    )

    agent_payload = result.config.to_dict()["agents"]["ops"]
    assert agent_payload["provider_config_overrides"] == {"temperature": 0.4}
    runtime_config = build_runtime_config(result.config, agent_id="ops")
    assert runtime_config.providers.openai.model == "gpt-4.1-mini"
    assert runtime_config.providers.openai.base_url == "https://api.openai.com/v1"
    assert runtime_config.providers.openai.api_key == ""
    assert runtime_config.providers.openai.api_key_env == "OPENAI_API_KEY"


def test_empty_model_uses_recommended_provenance(tmp_path: Path) -> None:
    result = build_provider_setup(
        ProviderSetupRequest(
            preset_id="openai",
            agent_id="ops",
            model="",
            config_path=str(tmp_path / ".openminion" / "agents.json"),
            home_root=tmp_path,
            data_root=tmp_path / ".openminion",
            env={"OPENAI_API_KEY": "sk-live"},
        )
    )

    assert result.model_choice.selected_model == "gpt-4.1-mini"
    assert result.model_choice.source == "recommended"
    assert result.preview.model_source == "recommended"


def test_setup_makes_selected_agent_runnable_without_overwriting_channel(
    tmp_path: Path,
) -> None:
    existing = OpenMinionConfig()
    existing.agents = {
        "new": AgentProfileConfig(name="new", provider="echo"),
        "custom": AgentProfileConfig(
            name="custom",
            provider="echo",
            default_channel="webhook",
        ),
    }

    new_result = build_provider_setup(
        ProviderSetupRequest(
            preset_id="ollama",
            agent_id="new",
            config_path=str(tmp_path / "agents.json"),
            data_root=tmp_path,
            env={},
        ),
        existing_config=existing,
    )
    custom_result = build_provider_setup(
        ProviderSetupRequest(
            preset_id="ollama",
            agent_id="custom",
            config_path=str(tmp_path / "agents.json"),
            data_root=tmp_path,
            env={},
        ),
        existing_config=existing,
    )

    assert new_result.config.agents["new"].default_channel == "console"
    assert custom_result.config.agents["custom"].default_channel == "webhook"


def test_explicit_existing_config_preserves_configured_model_provenance(
    tmp_path: Path,
) -> None:
    existing = OpenMinionConfig()
    existing.storage.path = str(tmp_path / "existing.db")
    existing.providers.openai.model = "configured-model"

    result = build_provider_setup(
        ProviderSetupRequest(
            preset_id="openai",
            agent_id="ops",
            config_path=str(tmp_path / ".openminion" / "agents.json"),
            home_root=tmp_path,
            data_root=tmp_path / ".openminion",
            env={"OPENAI_API_KEY": "sk-live"},
        ),
        existing_config=existing,
    )

    assert result.model_choice.selected_model == "configured-model"
    assert result.model_choice.source == "configured"
    assert result.config.storage.path == str(tmp_path / "existing.db")


def test_local_credential_requires_explicit_storage_consent() -> None:
    preset = get_setup_preset("openai")

    with pytest.raises(ProviderSetupError):
        resolve_setup_credential(
            preset=preset,
            env={},
            stored_api_key="sk-local",
            allow_local_api_key=False,
        )


def test_explicit_empty_env_does_not_read_process_environment() -> None:
    preset = get_setup_preset("openai")

    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-process"}):
        with pytest.raises(ProviderSetupError, match="Missing credential"):
            resolve_setup_credential(preset=preset, env={})


def test_redacted_payload_hides_local_credentials(tmp_path: Path) -> None:
    result = build_provider_setup(
        ProviderSetupRequest(
            preset_id="anthropic",
            agent_id="ops",
            model="claude-sonnet-5",
            stored_api_key="anthropic-local",
            allow_local_api_key=True,
            config_path=str(tmp_path / ".openminion" / "agents.json"),
            home_root=tmp_path,
            data_root=tmp_path / ".openminion",
            env={},
        )
    )

    redacted = redacted_config_payload(result.config)
    assert redacted["providers"]["anthropic"]["api_key"] == "<redacted>"
    assert "anthropic-local" not in json.dumps(redacted)


def test_shared_adapter_setup_uses_selected_agent_overrides(tmp_path: Path) -> None:
    existing = OpenMinionConfig()
    existing.agents = {
        "openai-main": AgentProfileConfig(name="openai-main", provider="openai")
    }
    existing.default_agent = "openai-main"
    existing.providers.openai.model = "gpt-4.1-mini"
    existing.providers.openai.base_url = "https://api.openai.com/v1"
    existing.providers.openai.api_key_env = "OPENAI_API_KEY"

    result = build_provider_setup(
        ProviderSetupRequest(
            preset_id="minimax",
            agent_id="minimax-m2-7",
            model="MiniMax-M2.7",
            config_path=str(tmp_path / ".openminion" / "agents.json"),
            home_root=tmp_path,
            data_root=tmp_path / ".openminion",
            env={"MINIMAX_API_KEY": "sk-mini"},
        ),
        existing_config=existing,
    )

    payload = result.config.to_dict()
    assert payload["providers"]["openai"]["api_key_env"] == "OPENAI_API_KEY"
    assert payload["providers"]["openai"]["base_url"] == "https://api.openai.com/v1"
    overrides = payload["agents"]["minimax-m2-7"]["provider_config_overrides"]
    assert overrides["api_key_env"] == "MINIMAX_API_KEY"
    assert overrides["base_url"] == "https://api.minimax.io/v1"
    assert overrides["model"] == "MiniMax-M2.7"
    assert overrides["api_key"] == ""
    assert overrides["provider_identity"] == {
        "transport_adapter": "openai_chat",
        "wire_protocol_family": "openai_chat_completions",
        "service_vendor": "minimax",
        "model_family": "minimax",
    }


@pytest.mark.parametrize(
    ("preset_id", "model", "credential_env", "base_url"),
    [
        ("minimax", "MiniMax-M3", "MINIMAX_API_KEY", "https://api.minimax.io/v1"),
        ("kimi", "kimi-k3", "MOONSHOT_API_KEY", "https://api.moonshot.ai/v1"),
        ("zai", "glm-5.2", "ZAI_API_KEY", "https://api.z.ai/api/paas/v4/"),
        (
            "zai-coding",
            "glm-5.2",
            "ZAI_API_KEY",
            "https://api.z.ai/api/coding/paas/v4",
        ),
        (
            "deepseek",
            "deepseek-v4-flash",
            "DEEPSEEK_API_KEY",
            "https://api.deepseek.com",
        ),
        (
            "qwen-dashscope",
            "qwen-plus",
            "DASHSCOPE_API_KEY",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        (
            "gemini",
            "gemini-3.6-flash",
            "GEMINI_API_KEY",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
        ),
        ("xai", "grok-4.5", "XAI_API_KEY", "https://api.x.ai/v1"),
        (
            "mistral",
            "mistral-large-latest",
            "MISTRAL_API_KEY",
            "https://api.mistral.ai/v1",
        ),
        (
            "together",
            "MiniMaxAI/MiniMax-M3",
            "TOGETHER_API_KEY",
            "https://api.together.ai/v1",
        ),
    ],
)
def test_frontier_shared_adapter_presets_use_selected_agent_overrides(
    tmp_path: Path,
    preset_id: str,
    model: str,
    credential_env: str,
    base_url: str,
) -> None:
    existing = OpenMinionConfig()
    existing.agents = {
        "openai-main": AgentProfileConfig(name="openai-main", provider="openai")
    }
    existing.default_agent = "openai-main"
    existing.providers.openai.model = "gpt-4.1-mini"
    existing.providers.openai.base_url = "https://api.openai.com/v1"
    existing.providers.openai.api_key_env = "OPENAI_API_KEY"

    result = build_provider_setup(
        ProviderSetupRequest(
            preset_id=preset_id,
            agent_id=f"{preset_id}-agent",
            model=model,
            config_path=str(tmp_path / ".openminion" / "agents.json"),
            home_root=tmp_path,
            data_root=tmp_path / ".openminion",
            env={credential_env: "sk-provider"},
        ),
        existing_config=existing,
    )

    payload = result.config.to_dict()
    assert payload["providers"]["openai"]["api_key_env"] == "OPENAI_API_KEY"
    assert payload["providers"]["openai"]["base_url"] == "https://api.openai.com/v1"
    overrides = payload["agents"][f"{preset_id}-agent"]["provider_config_overrides"]
    assert overrides["api_key_env"] == credential_env
    assert overrides["base_url"] == base_url
    assert overrides["model"] == model
    assert overrides["api_key"] == ""
    assert overrides["provider_identity"]["transport_adapter"] == "openai_chat"
    assert overrides["provider_identity"]["wire_protocol_family"] == (
        "openai_chat_completions"
    )
    assert overrides["provider_identity"]["service_vendor"] != "openai"


def test_shared_adapter_setup_replaces_stale_selected_agent_overrides(
    tmp_path: Path,
) -> None:
    existing = OpenMinionConfig()
    existing.agents = {
        "openai-main": AgentProfileConfig(name="openai-main", provider="openai"),
        "minimax-m2-7": AgentProfileConfig(
            name="minimax-m2-7",
            provider="openai",
            provider_config_overrides={
                "api_key": "old-minimax-secret",
                "api_key_env": "OLD_MINIMAX_KEY",
                "base_url": "https://old-minimax.example.invalid/v1",
                "model": "old-minimax-model",
                "temperature": 0.1,
            },
        ),
    }
    existing.default_agent = "openai-main"

    result = build_provider_setup(
        ProviderSetupRequest(
            preset_id="minimax",
            agent_id="minimax-m2-7",
            model="MiniMax-M2.7",
            config_path=str(tmp_path / ".openminion" / "agents.json"),
            home_root=tmp_path,
            data_root=tmp_path / ".openminion",
            env={"MINIMAX_API_KEY": "sk-mini"},
        ),
        existing_config=existing,
    )

    overrides = result.config.to_dict()["agents"]["minimax-m2-7"][
        "provider_config_overrides"
    ]
    assert overrides == {
        "api_key": "",
        "api_key_env": "MINIMAX_API_KEY",
        "base_url": "https://api.minimax.io/v1",
        "model": "MiniMax-M2.7",
        "provider_identity": {
            "transport_adapter": "openai_chat",
            "wire_protocol_family": "openai_chat_completions",
            "service_vendor": "minimax",
            "model_family": "minimax",
        },
        "temperature": 0.1,
    }


def test_minimax_and_openrouter_keep_separate_credential_references(
    tmp_path: Path,
) -> None:
    shared_fixture_value = "same-fixture-value-not-for-network-use"
    config_path = str(tmp_path / ".openminion" / "agents.json")
    openrouter = build_provider_setup(
        ProviderSetupRequest(
            preset_id="openrouter",
            agent_id="openrouter-agent",
            model="openai/gpt-4.1-mini",
            config_path=config_path,
            home_root=tmp_path,
            data_root=tmp_path / ".openminion",
            env={"OPENROUTER_API_KEY": shared_fixture_value},
        )
    )
    minimax = build_provider_setup(
        ProviderSetupRequest(
            preset_id="minimax",
            agent_id="minimax-agent",
            model="MiniMax-M2.7",
            config_path=config_path,
            home_root=tmp_path,
            data_root=tmp_path / ".openminion",
            env={"MINIMAX_API_KEY": shared_fixture_value},
        ),
        existing_config=openrouter.config,
    )

    payload = minimax.config.to_dict()
    assert payload["providers"]["openrouter"]["api_key_env"] == ("OPENROUTER_API_KEY")
    assert payload["providers"]["openai"]["api_key_env"] == "MINIMAX_API_KEY"
    assert payload["providers"]["openai"]["base_url"] == ("https://api.minimax.io/v1")
    assert shared_fixture_value not in json.dumps(payload)


def test_claude_alias_counts_as_shared_anthropic_adapter(tmp_path: Path) -> None:
    existing = OpenMinionConfig()
    existing.agents = {
        "legacy-claude": AgentProfileConfig(name="legacy-claude", provider="claude")
    }
    existing.default_agent = "legacy-claude"
    existing.providers.anthropic.model = "legacy-claude-model"

    result = build_provider_setup(
        ProviderSetupRequest(
            preset_id="anthropic",
            agent_id="anthropic-new",
            model="claude-sonnet-5",
            config_path=str(tmp_path / ".openminion" / "agents.json"),
            home_root=tmp_path,
            data_root=tmp_path / ".openminion",
            env={"ANTHROPIC_API_KEY": "sk-anthropic"},
        ),
        existing_config=existing,
    )

    payload = result.config.to_dict()
    assert result.preview.shared_adapter_isolated is True
    assert payload["providers"]["anthropic"]["model"] == "legacy-claude-model"
    assert (
        payload["agents"]["anthropic-new"]["provider_config_overrides"]["model"]
        == "claude-sonnet-5"
    )


def test_setup_rejects_incomplete_base_url(tmp_path: Path) -> None:
    with pytest.raises(ProviderSetupError, match="hostname"):
        build_provider_setup(
            ProviderSetupRequest(
                preset_id="custom-openai-compatible",
                agent_id="ops",
                model="model",
                base_url="https://",
                config_path=str(tmp_path / ".openminion" / "agents.json"),
                home_root=tmp_path,
                data_root=tmp_path / ".openminion",
                env={"OPENAI_COMPATIBLE_API_KEY": "sk-custom"},
            )
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode contract")
def test_atomic_setup_save_uses_owner_only_permissions(tmp_path: Path) -> None:
    config = OpenMinionConfig()
    config.agents = {"ops": AgentProfileConfig(name="ops", provider="echo")}
    config.default_agent = "ops"
    path = tmp_path / ".openminion" / "agents.json"

    saved = atomic_save_setup_config(config, path)

    assert saved == path.resolve(strict=False)
    assert (path.parent.stat().st_mode & 0o777) == 0o700
    assert (path.stat().st_mode & 0o777) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode contract")
def test_atomic_setup_save_preserves_existing_explicit_directory_mode(
    tmp_path: Path,
) -> None:
    config = OpenMinionConfig()
    config.agents = {"ops": AgentProfileConfig(name="ops", provider="echo")}
    config.default_agent = "ops"
    config_dir = tmp_path / "operator-selected-config-dir"
    config_dir.mkdir()
    os.chmod(config_dir, 0o755)
    path = config_dir / "agents.json"

    atomic_save_setup_config(config, path)

    assert (config_dir.stat().st_mode & 0o777) == 0o755
    assert (path.stat().st_mode & 0o777) == 0o600


def test_atomic_setup_save_preserves_original_on_validation_failure(
    tmp_path: Path,
) -> None:
    config = OpenMinionConfig()
    config.agents = {"ops": AgentProfileConfig(name="ops", provider="echo")}
    config.default_agent = "ops"
    path = tmp_path / ".openminion" / "agents.json"
    atomic_save_setup_config(config, path)
    before = path.read_text(encoding="utf-8")

    with mock.patch(
        "openminion.services.bootstrap.provider_setup._parse_config_file",
        side_effect=RuntimeError("parse failed"),
    ):
        with pytest.raises(RuntimeError):
            atomic_save_setup_config(config, path)

    assert path.read_text(encoding="utf-8") == before


def test_atomic_setup_save_preserves_original_on_final_mode_failure(
    tmp_path: Path,
) -> None:
    config = OpenMinionConfig()
    config.agents = {"ops": AgentProfileConfig(name="ops", provider="echo")}
    config.default_agent = "ops"
    path = tmp_path / ".openminion" / "agents.json"
    atomic_save_setup_config(config, path)
    before = path.read_text(encoding="utf-8")
    config.runtime.demo_mode = True

    with mock.patch(
        "openminion.services.bootstrap.provider_setup._apply_final_mode",
        side_effect=ProviderSetupError("mode failed"),
    ):
        with pytest.raises(ProviderSetupError):
            atomic_save_setup_config(config, path)

    assert path.read_text(encoding="utf-8") == before
