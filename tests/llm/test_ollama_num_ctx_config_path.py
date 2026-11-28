"""`num_ctx` from a parsed configuration reaches the Ollama request body (#725)."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

import pytest

from openminion.base.config import (
    OpenMinionConfig,
    RunProfileOverrides,
    build_runtime_config,
)
from openminion.modules.llm.config import LLMCTLConfig, resolve_provider_config
from openminion.modules.llm.providers.factory import build_runtime_llm_handle
from openminion.modules.llm.schemas import LLMRequest

_UNSET = object()


def _config(
    provider: Any = _UNSET, connection: Any = _UNSET, agent: Any = _UNSET
) -> dict[str, Any]:
    ollama: dict[str, Any] = {"model": "qwen2.5:7b"}
    if provider is not _UNSET:
        ollama["num_ctx"] = provider
    local_ollama: dict[str, Any] = {"provider": "ollama", "models": ["qwen2.5:7b"]}
    if connection is not _UNSET:
        local_ollama["provider_config_overrides"] = {"num_ctx": connection}
    profile: dict[str, Any] = {
        "name": "local",
        "provider": "ollama",
        "model_connections": {"local-ollama": local_ollama},
    }
    if agent is not _UNSET:
        profile["provider_config_overrides"] = {"num_ctx": agent}
    return {
        "default_agent": "local",
        "providers": {"ollama": ollama},
        "agents": {"local": profile},
    }


def _send(payload: dict[str, Any], provider_override: str = ""):
    runtime_config = build_runtime_config(
        OpenMinionConfig.from_dict(payload),
        agent_id="local",
        overrides=RunProfileOverrides(provider=provider_override),
    )
    handle = build_runtime_llm_handle(runtime_config, logging.getLogger(__name__))
    bodies: list[dict[str, Any]] = []

    def _post(**kwargs: Any) -> dict[str, Any]:
        bodies.append(kwargs["payload"])
        return {
            "model": "qwen2.5:7b",
            "message": {"role": "assistant", "content": "ok"},
            "prompt_eval_count": 3,
            "eval_count": 1,
        }

    request = LLMRequest.model_validate(
        {"messages": [{"role": "user", "content": "hello"}], "temperature": 0.1}
    )
    try:
        with patch(
            "openminion.modules.llm.providers.ollama.adapter._http_json_post",
            side_effect=_post,
        ):
            response = handle.client.call_sync(request=request)
    finally:
        handle.close()
    return response, bodies


@pytest.mark.parametrize(
    ("payload", "provider_override", "expected"),
    [
        (_config(provider=8192), "", 8192),
        (_config(provider=8192, connection=16384), "local-ollama", 16384),
        (_config(provider=8192, connection=0), "local-ollama", None),
        (_config(), "", None),
        (_config(provider=0), "", None),
        (_config(provider=8192, agent=4096), "", 4096),
        (_config(provider=8192, agent=4096), "ollama", 8192),
    ],
    ids=[
        "provider",
        "connection-over-provider",
        "connection-zero-clears-provider",
        "omitted",
        "provider-zero",
        "agent-patch",
        "explicit-provider-bypasses-agent-patch",
    ],
)
def test_parsed_num_ctx_reaches_the_request_body(
    payload: dict[str, Any], provider_override: str, expected: int | None
) -> None:
    response, bodies = _send(payload, provider_override)

    assert response.ok
    assert len(bodies) == 1
    options = bodies[0]["options"]
    assert options.get("num_ctx") == expected
    assert ("num_ctx" in options) is (expected is not None)
    assert options["temperature"] == 0.1


@pytest.mark.parametrize(
    ("payload", "provider_override"),
    [
        (_config(provider=True), ""),
        (_config(provider="16384"), ""),
        (_config(connection=-1), "local-ollama"),
        (_config(provider=None), ""),
        (_config(provider=8192, connection=None), "local-ollama"),
        (_config(provider=8192, agent=None), ""),
    ],
    ids=[
        "provider-bool",
        "provider-string",
        "connection-negative",
        "provider-null",
        "connection-null",
        "agent-null",
    ],
)
def test_invalid_parsed_num_ctx_fails_before_sending(
    payload: dict[str, Any], provider_override: str
) -> None:
    response, bodies = _send(payload, provider_override)

    assert not response.ok
    assert response.error is not None
    assert response.error.code == "INVALID_ARGUMENT"
    assert "num_ctx" in response.error.message
    assert bodies == []


def test_resolve_provider_config_keeps_explicit_null_extensions_only() -> None:
    config = LLMCTLConfig.model_validate(
        {
            "providers": {
                "ollama": {"base_url": None, "num_ctx": None, "model": "qwen2.5:7b"},
                "openai": {"base_url": None, "model": "gpt-4.1-mini", "timeout_s": 30},
            }
        }
    )

    assert resolve_provider_config(config, "ollama") == {
        "headers": {},
        "num_ctx": None,
        "model": "qwen2.5:7b",
    }
    openai = resolve_provider_config(config, "openai")
    assert "base_url" not in openai
    assert openai["timeout_s"] == 30
    assert None not in openai.values()
