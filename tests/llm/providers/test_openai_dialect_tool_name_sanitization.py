from __future__ import annotations

import re
from typing import Any
from unittest.mock import patch

import pytest

from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.providers.openai.adapter import OpenAIProvider
from openminion.modules.llm.providers.openrouter.adapter import OpenRouterProvider
from openminion.modules.llm.providers.tool_calling.capabilities import (
    build_tool_schema_name_map,
    remap_provider_tool_call_name,
)
from openminion.modules.llm.schemas import LLMRequest
from openminion.modules.tool import build_default_tool_registry


_SAFE_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def test_every_dotted_canonical_name_round_trips() -> None:
    registry = build_default_tool_registry()
    provider_specs = registry.provider_specs()
    name_map = build_tool_schema_name_map(
        provider_specs,
        provider_name="openrouter",
        model_name="openai/gpt-4o-mini",
    )

    dotted_names = sorted(
        {
            spec.name.strip()
            for spec in provider_specs
            if "." in str(spec.name or "").strip()
        }
    )
    assert dotted_names

    for canonical_name in dotted_names:
        external_name = name_map.canonical_to_external.get(canonical_name)
        assert external_name, f"missing sanitized name for {canonical_name!r}"
        assert _SAFE_TOOL_NAME_RE.fullmatch(external_name), (
            f"sanitized {canonical_name!r} -> {external_name!r} fails OpenAI regex"
        )
        recovered_name = remap_provider_tool_call_name(
            external_name,
            external_to_canonical=name_map.external_to_canonical,
        )
        assert recovered_name == canonical_name


def test_no_collisions_in_current_registry() -> None:
    registry = build_default_tool_registry()
    provider_specs = registry.provider_specs()
    name_map = build_tool_schema_name_map(
        provider_specs,
        provider_name="openai",
        model_name="gpt-4o-mini",
    )

    canonical_names = sorted(
        {
            str(spec.name or "").strip()
            for spec in provider_specs
            if str(spec.name or "").strip()
        }
    )
    external_names = [name_map.external_name_for(name) for name in canonical_names]
    assert len(set(external_names)) == len(canonical_names)


def test_collision_check_raises_with_synthetic_collision() -> None:
    from openminion.modules.llm.providers.base import ProviderToolSpec

    with pytest.raises(LLMCtlError) as excinfo:
        build_tool_schema_name_map(
            [
                ProviderToolSpec(name="git.diff", description="Diff"),
                ProviderToolSpec(name="git_diff", description="Diff alias"),
            ],
            provider_name="openrouter",
            model_name="openai/gpt-4o-mini",
        )

    message = str(excinfo.value)
    assert "git.diff" in message
    assert "git_diff" in message
    assert "git_diff" in message


def test_undotted_names_pass_through_unchanged() -> None:
    from openminion.modules.llm.providers.base import ProviderToolSpec

    name_map = build_tool_schema_name_map(
        [
            ProviderToolSpec(name="weather", description="Weather"),
            ProviderToolSpec(name="submit_output", description="Submit"),
        ],
        provider_name="openai",
        model_name="gpt-4o-mini",
    )

    assert name_map.canonical_to_external == {}
    assert name_map.external_to_canonical == {}
    assert name_map.external_name_for("weather") == "weather"
    assert name_map.external_name_for("submit_output") == "submit_output"


def _request_with_dotted_tool() -> LLMRequest:
    return LLMRequest.model_validate(
        {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "search for release notes"}],
            "tools": [
                {
                    "name": "web.search",
                    "description": "Search the web",
                    "input_schema": {"type": "object"},
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "web.search"}},
        }
    )


def _tool_call_payload(tool_name: str) -> dict[str, Any]:
    return {
        "model": "gpt-4o-mini",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": '{"query":"release notes"}',
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def test_openai_provider_sanitizes_outbound_names_and_recovers_canonical_name() -> None:
    provider = OpenAIProvider()
    request = _request_with_dotted_tool()
    captured_payload: dict[str, Any] = {}

    def _fake_post(**kwargs: Any) -> dict[str, Any]:
        captured_payload.update(kwargs["payload"])
        return _tool_call_payload("web_search")

    with patch(
        "openminion.modules.llm.providers.openai.adapter._http_json_post",
        side_effect=_fake_post,
    ):
        response = provider.complete(
            request,
            {
                "api_key": "test-key",
                "base_url": "https://api.openai.com/v1",
                "tool_call_strategy": "native",
            },
        )

    assert captured_payload["tools"][0]["function"]["name"] == "web_search"
    assert captured_payload["tool_choice"]["function"]["name"] == "web_search"
    assert response.tool_calls[0].name == "web.search"
    assert response.telemetry["normalization"]["behavior_profile_id"] == "default"
    assert response.telemetry["normalization"]["request_dialect"] == "openai_default"
    assert response.telemetry["normalization"]["tool_schema_capability"] == (
        "openai_dialect_safe_names"
    )


def test_openrouter_provider_sanitizes_outbound_names_and_recovers_canonical_name() -> (
    None
):
    provider = OpenRouterProvider()
    request = _request_with_dotted_tool()
    captured_payload: dict[str, Any] = {}

    def _fake_post(**kwargs: Any) -> dict[str, Any]:
        captured_payload.update(kwargs["payload"])
        return _tool_call_payload("web_search")

    with patch(
        "openminion.modules.llm.providers.openrouter.adapter._http_json_post",
        side_effect=_fake_post,
    ):
        response = provider.complete(
            request,
            {
                "api_key": "test-key",
                "base_url": "https://openrouter.ai/api/v1",
                "tool_call_strategy": "native",
            },
        )

    assert captured_payload["tools"][0]["function"]["name"] == "web_search"
    assert captured_payload["tool_choice"]["function"]["name"] == "web_search"
    assert response.tool_calls[0].name == "web.search"
    assert response.telemetry["normalization"]["behavior_profile_id"] == "default"
    assert response.telemetry["normalization"]["tool_schema_capability"] == (
        "openai_dialect_safe_names"
    )
