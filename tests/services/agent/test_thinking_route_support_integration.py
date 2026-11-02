from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.modules.llm.providers.base import ProviderRequest, ProviderToolSpec
from openminion.modules.llm.providers.behavior import resolve_behavior_profile
from openminion.modules.llm.providers.bridge import LLMCTLBridgeProvider
from openminion.modules.llm.reasoning import (
    ThinkingCtl,
    ThinkingRequest,
    ThinkingResolutionInput,
)
from openminion.services.agent import AgentService
from openminion.services.runtime.plugins import PluginRegistry

_SUBMIT_OUTPUT_TOOL_CHOICE = {"type": "function", "function": {"name": "submit_output"}}
_SUBMIT_OUTPUT_TOOL = ProviderToolSpec(
    name="submit_output",
    description="Return structured output.",
    parameters={"type": "object"},
)


def _brain_metadata(*, provider: str, profile: str) -> dict[str, str]:
    """Simulate the brain's pre-resolved thinking metadata (no route known)."""
    ctl = ThinkingCtl()
    resolved = ctl.resolve(
        request=ThinkingRequest(
            purpose="decide",
            requested_profile=profile,
            provider=provider,
            model="example-model",
        ),
        layers=ThinkingResolutionInput(code_default_profile="minimal"),
    )
    return ctl.build_provider_metadata(resolved=resolved)


# --- Bridge owner fakes -----------------------------------------------------


class _BridgeAgentProfile:
    def __init__(self, name, default_provider, default_model, tool_policy=None):
        del name, default_provider, default_model, tool_policy


class _BridgeToolPolicy:
    def __init__(self, **kwargs):
        pass


class _BridgeOkResponse:
    def __init__(self):
        self.ok = True
        self.error = None
        self.output_text = ""
        self.model = "bridge-model"
        self.usage = SimpleNamespace(
            input_tokens=1, output_tokens=1, total_tokens=2, total_source="provider"
        )
        self.tool_calls = []
        self.finish_reason = "stop"
        self.provider_raw = {}


class _BridgeErrorResponse:
    def __init__(self):
        self.ok = False
        self.error = SimpleNamespace(
            code="PROVIDER_ERROR",
            message=(
                "openai request failed with HTTP 400: The tool_choice parameter "
                "does not support being set to required or object in thinking mode"
            ),
        )
        self.output_text = ""
        self.model = "bridge-model"
        self.usage = SimpleNamespace(input_tokens=0, output_tokens=0, total_tokens=0)
        self.tool_calls = []
        self.finish_reason = ""
        self.provider_raw = {}


class _CapturingBridgeClient:
    def __init__(self, *, retry: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self._retry = retry

    def complete(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self._retry and len(self.calls) == 1:
            return _BridgeErrorResponse()
        return _BridgeOkResponse()


class _CapturingBridgeLLMCTL:
    retry = False

    @classmethod
    def from_config(cls, config):
        del config
        return cls()

    def client(self, profile):
        del profile
        client = _CapturingBridgeClient(retry=self.retry)
        self._client = client
        return client


def _bridge(*, provider_name: str) -> LLMCTLBridgeProvider:
    with patch(
        "openminion.modules.llm.providers.bridge._import_openminion_llm",
        return_value=(_BridgeAgentProfile, _CapturingBridgeLLMCTL, _BridgeToolPolicy),
    ):
        return LLMCTLBridgeProvider(
            provider_name=provider_name,
            model="example-model",
            provider_config={"api_key": "test-key"},
        )


# --- AgentService owner fakes -----------------------------------------------


class _RuntimeOkResponse:
    def __init__(self):
        self.ok = True
        self.error = None
        self.output_text = ""
        self.model = "example-model"
        self.usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        self.tool_calls = []
        self.finish_reason = "stop"
        self.contract_version = "v1"


class _RuntimeErrorResponse:
    def __init__(self):
        self.ok = False
        self.error = SimpleNamespace(
            code="PROVIDER_ERROR",
            message=(
                "openai request failed with HTTP 400: The tool_choice parameter "
                "does not support being set to required or object in thinking mode"
            ),
        )
        self.output_text = ""
        self.model = "example-model"
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.tool_calls = []
        self.finish_reason = ""
        self.contract_version = "v1"


class _CapturingRuntimeClient:
    def __init__(self, *, retry: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self._retry = retry

    def complete(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self._retry and len(self.calls) == 1:
            return _RuntimeErrorResponse()
        return _RuntimeOkResponse()


def _service(*, provider_name: str, client) -> AgentService:
    retry_policy = resolve_behavior_profile(
        provider=provider_name,
        model="example-model",
        base_url="",
    ).retry_override_policy
    runtime = SimpleNamespace(
        client=client,
        name=provider_name,
        model="example-model",
        retry_override_policy=retry_policy,
    )
    return AgentService(
        OpenMinionConfig(),
        PluginRegistry([]),
        None,
        logging.getLogger("openminion.tests.thinking_route_support"),
        llm_runtime=runtime,
    )


def _presolved_request(*, provider: str, profile: str, structured: bool = False):
    metadata = {
        "purpose": "decide",
        **_brain_metadata(provider=provider, profile=profile),
    }
    if structured:
        return ProviderRequest(
            user_message="route this",
            system_prompt="You are helpful.",
            tools=[_SUBMIT_OUTPUT_TOOL],
            tool_choice=_SUBMIT_OUTPUT_TOOL_CHOICE,
            metadata=metadata,
        )
    return ProviderRequest(
        user_message="hello",
        system_prompt="You are helpful.",
        metadata=metadata,
    )


# --- Bridge route integration tests -----------------------------------------


@pytest.mark.asyncio
async def test_bridge_presolved_supported_provider_transmits_effort() -> None:
    bridge = _bridge(provider_name="openai")

    await bridge.generate(_presolved_request(provider="openai", profile="detailed"))

    metadata = bridge._client.calls[-1]["metadata"]  # noqa: SLF001
    assert metadata["thinking_reasoning_profile"] == "detailed"
    assert metadata["thinking_supported"] == "true"
    assert metadata["thinking_provider_effort"] == "detailed"
    assert metadata["thinking"] == "detailed"
    assert "thinking_degraded_reason" not in metadata


@pytest.mark.asyncio
async def test_bridge_presolved_off_profile_forwards_no_effort() -> None:
    bridge = _bridge(provider_name="openai")

    await bridge.generate(_presolved_request(provider="openai", profile="off"))

    metadata = bridge._client.calls[-1]["metadata"]  # noqa: SLF001
    assert metadata["thinking_reasoning_profile"] == "off"
    assert metadata["thinking_supported"] == "true"
    assert "thinking_provider_effort" not in metadata
    assert "thinking" not in metadata


@pytest.mark.asyncio
async def test_bridge_presolved_unknown_provider_degrades() -> None:
    bridge = _bridge(provider_name="unsupported-provider")

    await bridge.generate(
        _presolved_request(provider="unsupported-provider", profile="detailed")
    )

    metadata = bridge._client.calls[-1]["metadata"]  # noqa: SLF001
    assert metadata["thinking_reasoning_profile"] == "detailed"
    assert metadata["thinking_supported"] == "false"
    assert "thinking_provider_effort" not in metadata
    assert metadata["thinking_degraded_reason"] == "provider_effort_unsupported"


@pytest.mark.asyncio
async def test_bridge_presolved_echo_is_not_transmitted() -> None:
    bridge = _bridge(provider_name="echo")

    await bridge.generate(_presolved_request(provider="echo", profile="detailed"))

    metadata = bridge._client.calls[-1]["metadata"]  # noqa: SLF001
    assert metadata["thinking_reasoning_profile"] == "detailed"
    assert metadata["thinking_supported"] == "false"
    assert "thinking_provider_effort" not in metadata
    assert metadata["thinking_degraded_reason"] == "provider_effort_unsupported"


@pytest.mark.asyncio
async def test_bridge_presolved_thinking_retry_control() -> None:
    _CapturingBridgeLLMCTL.retry = True
    try:
        bridge = _bridge(provider_name="openai")
    finally:
        _CapturingBridgeLLMCTL.retry = False

    await bridge.generate(
        _presolved_request(provider="openai", profile="minimal", structured=True)
    )

    assert [call["tool_choice"] for call in bridge._client.calls] == [  # noqa: SLF001
        _SUBMIT_OUTPUT_TOOL_CHOICE,
        "auto",
    ]


# --- Direct/ollama runtime route integration tests ---------------------------


def test_runtime_presolved_direct_openai_degrades() -> None:
    client = _CapturingRuntimeClient()
    service = _service(provider_name="openai", client=client)

    asyncio.run(
        service._invoke_provider_request(  # noqa: SLF001
            _presolved_request(provider="openai", profile="detailed")
        )
    )

    metadata = client.calls[0]["metadata"]
    assert metadata["thinking_reasoning_profile"] == "detailed"
    assert metadata["thinking_supported"] == "false"
    assert "thinking_provider_effort" not in metadata
    assert "thinking" not in metadata


def test_runtime_presolved_ollama_transmits_effort() -> None:
    client = _CapturingRuntimeClient()
    service = _service(provider_name="ollama", client=client)

    asyncio.run(
        service._invoke_provider_request(  # noqa: SLF001
            _presolved_request(provider="ollama", profile="detailed")
        )
    )

    metadata = client.calls[0]["metadata"]
    assert metadata["thinking_reasoning_profile"] == "detailed"
    assert metadata["thinking_supported"] == "true"
    assert metadata["thinking_provider_effort"] == "detailed"
    assert metadata["thinking"] == "detailed"


def test_runtime_presolved_off_profile_forwards_no_effort() -> None:
    client = _CapturingRuntimeClient()
    service = _service(provider_name="openai", client=client)

    asyncio.run(
        service._invoke_provider_request(  # noqa: SLF001
            _presolved_request(provider="openai", profile="off")
        )
    )

    metadata = client.calls[0]["metadata"]
    assert metadata["thinking_reasoning_profile"] == "off"
    assert metadata["thinking_supported"] == "true"
    assert "thinking_provider_effort" not in metadata
    assert "thinking" not in metadata


def test_runtime_presolved_direct_thinking_retry_control() -> None:
    client = _CapturingRuntimeClient(retry=True)
    service = _service(provider_name="openai", client=client)

    asyncio.run(
        service._invoke_provider_request(  # noqa: SLF001
            _presolved_request(provider="openai", profile="minimal", structured=True)
        )
    )

    assert [call["tool_choice"] for call in client.calls] == [
        _SUBMIT_OUTPUT_TOOL_CHOICE,
        "auto",
    ]
