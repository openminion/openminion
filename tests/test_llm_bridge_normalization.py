from __future__ import annotations

import pytest
from unittest.mock import patch

from openminion.modules.llm.providers.bridge import LLMCTLBridgeProvider
from openminion.modules.llm.providers.base import ProviderRequest, ProviderToolSpec
from openminion.modules.llm.providers.contracts import ProviderError


class _FakeUsage:
    def __init__(
        self, *, input_tokens: int = 0, output_tokens: int = 0, total_tokens: int = 0
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = total_tokens


class _FakeToolCall:
    def __init__(
        self,
        *,
        name: str,
        arguments: dict,
        call_id: str = "",
        status: str = "requested",
    ) -> None:
        self.id = call_id
        self.name = name
        self.arguments = arguments
        self.status = status


class _FakeLLMResponse:
    def __init__(self) -> None:
        self.ok = True
        self.error = None
        self.output_text = ""
        self.model = "bridge-model"
        self.usage = _FakeUsage(input_tokens=7, output_tokens=5, total_tokens=12)
        self.tool_calls = [
            _FakeToolCall(
                name="weather.openmeteo.current",
                arguments={"city": "Tokyo"},
                call_id="call-1",
            )
        ]
        # Canonical field should win over provider_raw.
        self.finish_reason = "tool_calls"
        self.provider_raw = {"choices": [{"finish_reason": "stop"}]}


class _FakeNestedSubmitOutputResponse:
    def __init__(self) -> None:
        self.ok = True
        self.error = None
        self.output_text = ""
        self.model = "bridge-model"
        self.usage = _FakeUsage(input_tokens=7, output_tokens=5, total_tokens=12)
        self.tool_calls = [
            _FakeToolCall(
                name="submit_output",
                arguments={
                    "decision": (
                        '{"mode":"respond","confidence":1.0,'
                        '"reason_code":"greeting","answer":"hello"}'
                    )
                },
                call_id="call-submit-1",
            )
        ]
        self.finish_reason = "tool_calls"
        self.provider_raw = {"choices": [{"finish_reason": "tool_calls"}]}


class _FakeStringifiedActTargetSubmitOutputResponse:
    def __init__(self) -> None:
        self.ok = True
        self.error = None
        self.output_text = ""
        self.model = "bridge-model"
        self.usage = _FakeUsage(input_tokens=7, output_tokens=5, total_tokens=12)
        self.tool_calls = [
            _FakeToolCall(
                name="submit_output",
                arguments={
                    "mode": "act",
                    "confidence": 0.95,
                    "reason_code": "weather_query",
                    "act_profile": "general",
                    "sub_intents": ["check_weather"],
                    "rationale": "Use the shared act loop for the weather lookup.",
                    "execution_target": (
                        '{"kind":"local","target_agent_id":"","target_capability":"",'
                        '"expect_async":false}'
                    ),
                },
                call_id="call-submit-string-command",
            )
        ]
        self.finish_reason = "tool_calls"
        self.provider_raw = {"choices": [{"finish_reason": "tool_calls"}]}


class _FakeStructuredAssistantMessage:
    def __init__(self, *, role: str, content) -> None:
        self.role = role
        self.content = content


class _FakeStructuredLLMResponse:
    def __init__(self) -> None:
        self.ok = True
        self.error = None
        self.output_text = ""
        self.model = "openrouter/oss20b"
        self.usage = _FakeUsage(input_tokens=9, output_tokens=6, total_tokens=15)
        self.tool_calls = []
        self.finish_reason = "end_turn"
        self.assistant_messages = [
            _FakeStructuredAssistantMessage(
                role="assistant",
                content=[
                    {
                        "type": "text",
                        "text": "Structured assistant output from OSS model.",
                    }
                ],
            )
        ]
        self.provider_raw = {}


class _FakeClient:
    def complete(self, **kwargs):
        del kwargs
        return _FakeLLMResponse()


class _FakeStructuredClient:
    def complete(self, **kwargs):
        del kwargs
        return _FakeStructuredLLMResponse()


class _FakeNestedSubmitOutputClient:
    def complete(self, **kwargs):
        del kwargs
        return _FakeNestedSubmitOutputResponse()


class _FakeStringifiedActTargetSubmitOutputClient:
    def complete(self, **kwargs):
        del kwargs
        return _FakeStringifiedActTargetSubmitOutputResponse()


class _FakeLLMCTL:
    @classmethod
    def from_config(cls, config):
        del config
        return cls()

    def client(self, profile):
        del profile
        return _FakeClient()


class _FakeStructuredLLMCTL:
    @classmethod
    def from_config(cls, config):
        del config
        return cls()

    def client(self, profile):
        del profile
        return _FakeStructuredClient()


class _FakeAgentProfile:
    def __init__(self, name, default_provider, default_model, tool_policy=None):
        del name, default_provider, default_model, tool_policy


class _FakeToolPolicy:
    def __init__(self, **kwargs):
        pass


class _FakeClientWithToolCapture:
    def __init__(self) -> None:
        self.captured_tools = None
        self.captured_tool_choice = None
        self.captured_metadata = None

    def complete(self, **kwargs):
        self.captured_tools = kwargs.get("tools")
        self.captured_tool_choice = kwargs.get("tool_choice")
        self.captured_metadata = kwargs.get("metadata")
        return _FakeLLMResponse()


class _FakeLLMCTLWithCapture:
    @classmethod
    def from_config(cls, config):
        del config
        return cls()

    def client(self, profile):
        del profile
        self._client = _FakeClientWithToolCapture()
        return self._client


class _FakeNestedSubmitOutputLLMCTL:
    @classmethod
    def from_config(cls, config):
        del config
        return cls()

    def client(self, profile):
        del profile
        return _FakeNestedSubmitOutputClient()


class _FakeStringifiedActTargetSubmitOutputLLMCTL:
    @classmethod
    def from_config(cls, config):
        del config
        return cls()

    def client(self, profile):
        del profile
        return _FakeStringifiedActTargetSubmitOutputClient()


class _FakeError:
    def __init__(self, *, code: str, message: str) -> None:
        self.code = code
        self.message = message


class _FakeLLMErrorResponse:
    def __init__(self, *, message: str) -> None:
        self.ok = False
        self.error = _FakeError(code="PROVIDER_ERROR", message=message)
        self.output_text = ""
        self.model = "bridge-model"
        self.usage = _FakeUsage()
        self.tool_calls = []
        self.finish_reason = ""
        self.provider_raw = {}


class _FakeRetryClient:
    def __init__(self) -> None:
        self.tool_choices: list[object] = []
        self.calls = 0

    def complete(self, **kwargs):
        self.calls += 1
        self.tool_choices.append(kwargs.get("tool_choice"))
        if self.calls == 1:
            return _FakeLLMErrorResponse(
                message=(
                    "openai request failed with HTTP 400: "
                    "The tool_choice parameter does not support being set to required "
                    "or object in thinking mode"
                )
            )
        return _FakeLLMResponse()


class _FakeLLMCTLWithRetry:
    @classmethod
    def from_config(cls, config):
        del config
        return cls()

    def client(self, profile):
        del profile
        self._client = _FakeRetryClient()
        return self._client


@pytest.mark.asyncio
async def test_llm_bridge_uses_canonical_finish_reason_and_normalization_flag() -> None:
    with patch(
        "openminion.modules.llm.providers.bridge._import_openminion_llm",
        return_value=(_FakeAgentProfile, _FakeLLMCTL, _FakeToolPolicy),
    ):
        bridge = LLMCTLBridgeProvider(
            provider_name="openrouter",
            model="openai/gpt-4.1-mini",
            provider_config={"api_key": "test-key"},
        )

    response = await bridge.generate(
        ProviderRequest(
            user_message="weather?",
            system_prompt="You are helpful.",
            metadata={"purpose": "plan"},
        )
    )

    assert response.finish_reason == "tool_calls"
    assert response.tool_calls
    assert response.tool_calls[0].name == "weather.openmeteo.current"
    assert response.normalization.get("tool_calls_normalized") is True
    assert response.normalization.get("adapter") == "llmctl_bridge"


@pytest.mark.asyncio
async def test_llm_bridge_extracts_structured_assistant_content_and_profile_aliases() -> (
    None
):
    with patch(
        "openminion.modules.llm.providers.bridge._import_openminion_llm",
        return_value=(_FakeAgentProfile, _FakeStructuredLLMCTL, _FakeToolPolicy),
    ):
        bridge = LLMCTLBridgeProvider(
            provider_name="openrouter",
            model="openrouter/oss20b",
            provider_config={"api_key": "test-key"},
        )

    response = await bridge.generate(
        ProviderRequest(
            user_message="hello?",
            system_prompt="You are helpful.",
            metadata={"purpose": "decide"},
        )
    )

    assert response.text == "Structured assistant output from OSS model."
    assert response.finish_reason == "stop"
    assert response.normalization.get("normalization_profile") == "openrouter-oss"


@pytest.mark.asyncio
async def test_llm_bridge_normalizes_nested_submit_output_arguments() -> None:
    with patch(
        "openminion.modules.llm.providers.bridge._import_openminion_llm",
        return_value=(
            _FakeAgentProfile,
            _FakeNestedSubmitOutputLLMCTL,
            _FakeToolPolicy,
        ),
    ):
        bridge = LLMCTLBridgeProvider(
            provider_name="openrouter",
            model="openai/gpt-4.1-mini",
            provider_config={"api_key": "test-key"},
        )

    response = await bridge.generate(
        ProviderRequest(
            user_message="hi",
            system_prompt="You are helpful.",
            tools=[
                ProviderToolSpec(
                    name="submit_output",
                    description="Return structured output.",
                    parameters={"type": "object"},
                )
            ],
            metadata={"purpose": "decide"},
        )
    )

    assert response.tool_calls
    assert response.tool_calls[0].name == "submit_output"
    assert response.tool_calls[0].arguments.get("mode") == "respond"
    assert response.tool_calls[0].arguments.get("reason_code") == "greeting"


@pytest.mark.asyncio
async def test_llm_bridge_decodes_stringified_execution_target_arguments() -> None:
    with patch(
        "openminion.modules.llm.providers.bridge._import_openminion_llm",
        return_value=(
            _FakeAgentProfile,
            _FakeStringifiedActTargetSubmitOutputLLMCTL,
            _FakeToolPolicy,
        ),
    ):
        bridge = LLMCTLBridgeProvider(
            provider_name="openrouter",
            model="openai/gpt-4.1-mini",
            provider_config={"api_key": "test-key"},
        )

    response = await bridge.generate(
        ProviderRequest(
            user_message="weather in sf",
            system_prompt="You are helpful.",
            tools=[
                ProviderToolSpec(
                    name="submit_output",
                    description="Return structured output.",
                    parameters={"type": "object"},
                )
            ],
            metadata={"purpose": "decide"},
        )
    )

    assert response.tool_calls
    target = response.tool_calls[0].arguments.get("execution_target")
    assert isinstance(target, dict)
    assert target.get("kind") == "local"


@pytest.mark.asyncio
async def test_llm_bridge_passes_tools_when_provided() -> None:
    with patch(
        "openminion.modules.llm.providers.bridge._import_openminion_llm",
        return_value=(_FakeAgentProfile, _FakeLLMCTLWithCapture, _FakeToolPolicy),
    ):
        bridge = LLMCTLBridgeProvider(
            provider_name="openrouter",
            model="openai/gpt-4.1-mini",
            provider_config={"api_key": "test-key"},
        )

    tools_in = [
        ProviderToolSpec(
            name="web.search",
            description="Search the web",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        ),
        ProviderToolSpec(
            name="weather",
            description="Get weather",
            parameters={
                "type": "object",
                "properties": {"location": {"type": "string"}},
            },
        ),
    ]

    # Access the internal client to verify tools passed
    client = bridge._client

    await bridge.generate(
        ProviderRequest(
            user_message="weather in Tokyo?",
            system_prompt="You are helpful.",
            tools=tools_in,
            metadata={"purpose": "plan"},
        )
    )

    assert client.captured_tools is not None, "tools should be passed to provider"
    assert len(client.captured_tools) == 2, "both tools should be passed"
    assert client.captured_tools[0]["name"] == "web.search"
    assert client.captured_tools[1]["name"] == "weather"


@pytest.mark.asyncio
async def test_llm_bridge_routes_thinking_metadata_through_shared_resolver() -> None:
    with patch(
        "openminion.modules.llm.providers.bridge._import_openminion_llm",
        return_value=(_FakeAgentProfile, _FakeLLMCTLWithCapture, _FakeToolPolicy),
    ):
        bridge = LLMCTLBridgeProvider(
            provider_name="openai",
            model="MiniMax-M2.5",
            provider_config={"api_key": "test-key"},
        )

    client = bridge._client

    await bridge.generate(
        ProviderRequest(
            user_message="hello",
            system_prompt="You are helpful.",
            thinking="detailed",
            metadata={"purpose": "decide"},
        )
    )

    assert client.captured_metadata["thinking_requested_profile"] == "detailed"
    assert client.captured_metadata["thinking_reasoning_profile"] == "detailed"
    assert client.captured_metadata["thinking_provider_effort"] == "detailed"
    assert client.captured_metadata["thinking_source_layer"] == "invocation_override"
    assert client.captured_metadata["thinking_supported"] == "true"
    assert client.captured_metadata["thinking"] == "detailed"


@pytest.mark.asyncio
async def test_llm_bridge_marks_unsupported_provider_effort_explicitly() -> None:
    with patch(
        "openminion.modules.llm.providers.bridge._import_openminion_llm",
        return_value=(_FakeAgentProfile, _FakeLLMCTLWithCapture, _FakeToolPolicy),
    ):
        bridge = LLMCTLBridgeProvider(
            provider_name="unsupported-provider",
            model="unsupported-model",
            provider_config={"api_key": "test-key"},
        )

    client = bridge._client

    await bridge.generate(
        ProviderRequest(
            user_message="hello",
            system_prompt="You are helpful.",
            thinking="detailed",
            metadata={"purpose": "decide"},
        )
    )

    assert client.captured_metadata["thinking_requested_profile"] == "detailed"
    assert client.captured_metadata["thinking_reasoning_profile"] == "detailed"
    assert client.captured_metadata["thinking_supported"] == "false"
    assert (
        client.captured_metadata["thinking_degraded_reason"]
        == "provider_effort_unsupported"
    )
    assert "thinking_provider_effort" not in client.captured_metadata
    assert "thinking" not in client.captured_metadata


@pytest.mark.asyncio
async def test_llm_bridge_strips_tools_when_empty() -> None:
    with patch(
        "openminion.modules.llm.providers.bridge._import_openminion_llm",
        return_value=(_FakeAgentProfile, _FakeLLMCTLWithCapture, _FakeToolPolicy),
    ):
        bridge = LLMCTLBridgeProvider(
            provider_name="openrouter",
            model="openai/gpt-4.1-mini",
            provider_config={"api_key": "test-key"},
        )

    client = bridge._client

    await bridge.generate(
        ProviderRequest(
            user_message="hello!",
            system_prompt="You are helpful.",
            tools=[],  # Empty tools
            metadata={"purpose": "chat"},
        )
    )

    # Service passes tools=None when list is empty (line 141 in service.py: tools=tools or None)
    assert client.captured_tools is None, (
        "tools should be None when empty list provided"
    )


@pytest.mark.asyncio
async def test_llm_bridge_preserves_function_targeted_tool_choice_dict() -> None:
    with patch(
        "openminion.modules.llm.providers.bridge._import_openminion_llm",
        return_value=(_FakeAgentProfile, _FakeLLMCTLWithCapture, _FakeToolPolicy),
    ):
        bridge = LLMCTLBridgeProvider(
            provider_name="openrouter",
            model="openai/gpt-4.1-mini",
            provider_config={"api_key": "test-key"},
        )

    client = bridge._client
    tool_choice = {"type": "function", "function": {"name": "submit_output"}}

    await bridge.generate(
        ProviderRequest(
            user_message="route this",
            system_prompt="You are helpful.",
            tool_choice=tool_choice,
            tools=[
                ProviderToolSpec(
                    name="submit_output",
                    description="Return structured output.",
                    parameters={"type": "object"},
                )
            ],
            metadata={"purpose": "decide"},
        )
    )

    assert client.captured_tool_choice == tool_choice


@pytest.mark.asyncio
async def test_llm_bridge_retries_with_auto_tool_choice_for_thinking_mode_constraint() -> (
    None
):
    with patch(
        "openminion.modules.llm.providers.bridge._import_openminion_llm",
        return_value=(_FakeAgentProfile, _FakeLLMCTLWithRetry, _FakeToolPolicy),
    ):
        bridge = LLMCTLBridgeProvider(
            provider_name="openai",
            model="MiniMax-M2.5",
            provider_config={"api_key": "test-key"},
        )

    client = bridge._client
    requested_tool_choice = {"type": "function", "function": {"name": "submit_output"}}

    response = await bridge.generate(
        ProviderRequest(
            user_message="hi",
            system_prompt="You are helpful.",
            tools=[
                ProviderToolSpec(
                    name="submit_output",
                    description="Return structured output.",
                    parameters={"type": "object"},
                )
            ],
            tool_choice=requested_tool_choice,
            metadata={"purpose": "decide", "thinking": "minimal"},
        )
    )

    assert response.tool_calls
    assert client.tool_choices[0] == requested_tool_choice
    assert client.tool_choices[1] == "auto"


@pytest.mark.asyncio
async def test_llm_bridge_override_retry_can_be_disabled_via_metadata() -> None:
    with patch(
        "openminion.modules.llm.providers.bridge._import_openminion_llm",
        return_value=(_FakeAgentProfile, _FakeLLMCTLWithRetry, _FakeToolPolicy),
    ):
        bridge = LLMCTLBridgeProvider(
            provider_name="openai",
            model="MiniMax-M2.5",
            provider_config={"api_key": "test-key"},
        )

    client = bridge._client
    with pytest.raises(ProviderError):
        await bridge.generate(
            ProviderRequest(
                user_message="hi",
                system_prompt="You are helpful.",
                tools=[
                    ProviderToolSpec(
                        name="submit_output",
                        description="Return structured output.",
                        parameters={"type": "object"},
                    )
                ],
                tool_choice={"type": "function", "function": {"name": "submit_output"}},
                metadata={
                    "purpose": "decide",
                    "thinking": "minimal",
                    "provider_override_mode": "disabled",
                },
            )
        )

    assert client.calls == 1
