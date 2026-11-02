from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from openminion.base.config import OpenMinionConfig, load_config, save_config


def _config_with_mcp_secrets() -> OpenMinionConfig:
    return OpenMinionConfig.from_dict(
        {
            "runtime": {
                "mcp_servers": [
                    {
                        "name": "private",
                        "command": ["private-mcp"],
                        "env": {"SAFE_FLAG": "1", "API_TOKEN": "stdio-secret"},
                        "env_secret_refs": {"DB_TOKEN": "secret://mcp/db"},
                    },
                    {
                        "name": "private-http",
                        "transport": "streamable_http",
                        "url": "https://mcp.example/messages",
                        "authorization": {
                            "mode": "bearer",
                            "bearer_token": "mcp-bearer-secret",
                        },
                    },
                    {
                        "name": "oauth",
                        "transport": "streamable_http",
                        "url": "https://oauth-mcp.example/messages",
                        "authorization": {
                            "mode": "oauth_pkce",
                            "client_id": "client-1",
                            "authorization_endpoint": "https://auth.example/authorize",
                            "token_endpoint": "https://auth.example/token",
                            "access_token": "mcp-access-secret",
                            "access_token_ref": "secret://mcp/oauth/access",
                            "refresh_token_ref": "secret://mcp/oauth/refresh",
                        },
                    },
                ]
            },
            "storage": {"path": "/tmp/openminion-persistence.db"},
            "custom_module": {"enabled": True, "label": "preserved"},
            "agents": {"default": {"provider": "echo"}},
            "default_agent": "default",
        }
    )


def test_public_config_serialization_redacts_mcp_secrets() -> None:
    payload = _config_with_mcp_secrets().to_dict()
    serialized = json.dumps(payload)

    stdio = payload["runtime"]["mcp_servers"][0]
    bearer_auth = payload["runtime"]["mcp_servers"][1]["authorization"]
    oauth_auth = payload["runtime"]["mcp_servers"][2]["authorization"]
    assert stdio["env"] == {"SAFE_FLAG": "<redacted>", "API_TOKEN": "<redacted>"}
    assert bearer_auth["mode"] == "bearer"
    assert bearer_auth["bearer_token"] == "<redacted>"
    assert oauth_auth["access_token"] == "<redacted>"
    assert "mcp-bearer-secret" not in serialized
    assert "mcp-access-secret" not in serialized
    assert "stdio-secret" not in serialized


def test_save_config_round_trip_preserves_mcp_secrets_and_unrelated_config(
    tmp_path: Path,
) -> None:
    config = _config_with_mcp_secrets()
    path = save_config(config, str(tmp_path / "agents.json"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    persisted = json.loads(path.read_text(encoding="utf-8"))
    authorizations = [
        server["authorization"] for server in persisted["runtime"]["mcp_servers"]
    ]
    assert persisted["runtime"]["mcp_servers"][0]["env"] == {
        "SAFE_FLAG": "1",
        "API_TOKEN": "stdio-secret",
    }
    assert authorizations[1]["bearer_token"] == "mcp-bearer-secret"
    assert authorizations[2]["access_token"] == "mcp-access-secret"
    assert authorizations[2]["access_token_ref"] == "secret://mcp/oauth/access"
    assert persisted["storage"]["path"] == "/tmp/openminion-persistence.db"
    assert persisted["custom_module"] == {"enabled": True, "label": "preserved"}

    reloaded = load_config(str(path))
    assert reloaded.runtime.mcp_servers[1].authorization.bearer_token == (
        "mcp-bearer-secret"
    )
    assert reloaded.runtime.mcp_servers[2].authorization.access_token == (
        "mcp-access-secret"
    )
    assert reloaded.runtime.mcp_servers[2].authorization.refresh_token_ref == (
        "secret://mcp/oauth/refresh"
    )
    assert reloaded.storage.path == "/tmp/openminion-persistence.db"
    assert reloaded.module_configs["custom_module"] == {
        "enabled": True,
        "label": "preserved",
    }


def test_save_config_skips_posix_permissions_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(os, "fchmod")

    path = save_config(_config_with_mcp_secrets(), str(tmp_path / "agents.json"))

    assert path.exists()


def _provider_payload(base_url: str, model: str = "gpt-4.1-mini") -> dict:
    return {
        "openai": {
            "base_url": base_url,
            "api_key_env": "GATEWAY_API_KEY",
            "model": model,
        }
    }


def test_saved_config_has_one_provider_authority(tmp_path: Path) -> None:
    config = OpenMinionConfig.from_dict({"providers": _provider_payload("https://gw/v1")})
    path = save_config(config, str(tmp_path / "agents.json"))

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["providers"]["openai"]["base_url"] == "https://gw/v1"
    assert "providers" not in persisted.get("system", {})


def test_editing_the_top_level_providers_block_is_effective(tmp_path: Path) -> None:
    config = OpenMinionConfig.from_dict({"providers": _provider_payload("https://old/v1")})
    path = save_config(config, str(tmp_path / "agents.json"))

    persisted = json.loads(path.read_text(encoding="utf-8"))
    persisted["providers"]["openai"]["base_url"] = "https://edited/v1"
    path.write_text(json.dumps(persisted), encoding="utf-8")

    reloaded = load_config(str(path))
    assert reloaded.providers.openai.base_url == "https://edited/v1"


def test_system_only_provider_config_still_loads() -> None:
    config = OpenMinionConfig.from_dict(
        {"system": {"providers": _provider_payload("https://system-only/v1")}}
    )

    assert config.providers.openai.base_url == "https://system-only/v1"


def test_mixed_provider_input_keeps_precedence_then_saves_one_authority(
    tmp_path: Path,
) -> None:
    config = OpenMinionConfig.from_dict(
        {
            "providers": _provider_payload("https://top-level/v1"),
            "system": {"providers": _provider_payload("https://system-wins/v1")},
        }
    )
    assert config.providers.openai.base_url == "https://system-wins/v1"

    path = save_config(config, str(tmp_path / "agents.json"))
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["providers"]["openai"]["base_url"] == "https://system-wins/v1"
    assert "providers" not in persisted["system"]

    assert load_config(str(path)).providers.openai.base_url == "https://system-wins/v1"
