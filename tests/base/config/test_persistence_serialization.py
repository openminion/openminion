from __future__ import annotations

import json
import stat
from pathlib import Path

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
