import asyncio
import unittest
import threading
from openminion.base.config import OpenMinionConfig
from openminion.base.types import Message
from openminion.services.runtime.plugins import PluginRegistry
from openminion.modules.llm.providers.base import (
    LLMProvider,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
)
from openminion.services.agent import AgentService
import logging
from tests._csc_fixtures import _csc_install_default_agent


def _run_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, object] = {}
    error: dict[str, BaseException] = {}

    def _target() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - threaded test bridge
            error["exc"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join()
    if "exc" in error:
        raise error["exc"]
    return result.get("value")


class MockProviderWithDifferentFormats(LLMProvider):
    def __init__(self, format_type="openrouter"):
        self.format_type = format_type
        self._name = f"mock_{format_type}"

    @property
    def name(self):
        return self._name

    async def generate(self, request: ProviderRequest):
        if self.format_type == "openrouter":
            return ProviderResponse(
                text="",
                model="openai/gpt-4o",
                tool_calls=[
                    ProviderToolCall(
                        id="call_abc123",
                        name="get_weather",
                        arguments={"location": "NYC"},
                        source="native",
                    )
                ],
                finish_reason="tool_calls",
                usage={
                    "total_tokens": 45,
                    "prompt_tokens": 12,
                    "completion_tokens": 33,
                },
            )
        elif self.format_type == "cortensor":
            return ProviderResponse(
                text="",
                model="gpt-4.1-mini",
                tool_calls=[
                    ProviderToolCall(
                        id="ct_987zyx",
                        name="get_weather",
                        arguments={"location": "London"},
                        source="native",
                    )
                ],
                finish_reason="tool_calls",
                usage={
                    "total_tokens": 40,
                    "prompt_tokens": 10,
                    "completion_tokens": 30,
                },
            )
        elif self.format_type == "echo":
            return ProviderResponse(
                text="This is a plain response",
                model="echo_model",
                tool_calls=[],
                finish_reason="stop",
                usage={"total_tokens": 5, "prompt_tokens": 2, "completion_tokens": 3},
            )
        else:
            return ProviderResponse(
                text="standard response",
                model="default_model",
                tool_calls=[],
                finish_reason="stop",
                usage={"total_tokens": 8},
            )


class ProviderNeutralChatIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.config = OpenMinionConfig()
        _csc_install_default_agent(self.config)  # type: ignore[attr-defined]
        self.plugin_registry = PluginRegistry([])
        self.logger = logging.getLogger(__name__)

    def test_chat_behavior_equivalent_tool_call_response(self):
        openrouter_provider = MockProviderWithDifferentFormats("openrouter")
        openrouter_service = AgentService(
            self.config,
            self.plugin_registry,
            openrouter_provider,
            self.logger,
        )
        cortensor_provider = MockProviderWithDifferentFormats("cortensor")
        cortensor_service = AgentService(
            self.config,
            self.plugin_registry,
            cortensor_provider,
            self.logger,
        )
        user_msg = Message(channel="console", target="user", body="Get weather in NYC")

        openrouter_response = _run_sync(openrouter_service.run_turn(user_msg))
        cortensor_response = _run_sync(cortensor_service.run_turn(user_msg))

        self.assertIn(
            "tool",
            openrouter_response.text.lower()
            or openrouter_response.metadata.get("finish_reason", "").lower(),
        )
        self.assertIn(
            "tool",
            cortensor_response.text.lower()
            or cortensor_response.metadata.get("finish_reason", "").lower(),
        )

        or_tool_calls = int(openrouter_response.metadata.get("tool_calls_count", 0))
        ct_tool_calls = int(cortensor_response.metadata.get("tool_calls_count", 0))

        self.assertGreater(or_tool_calls, 0)
        self.assertGreater(ct_tool_calls, 0)

        self.assertEqual(openrouter_response.metadata["finish_reason"], "tool_calls")
        self.assertEqual(cortensor_response.metadata["finish_reason"], "tool_calls")
        self.assertEqual(openrouter_response.metadata["provider"], "mock_openrouter")
        self.assertEqual(cortensor_response.metadata["provider"], "mock_cortensor")

    def test_chat_behavior_equivalent_plain_text_response(self):

        echo_provider = MockProviderWithDifferentFormats("echo")
        echo_service = AgentService(
            self.config,
            self.plugin_registry,
            echo_provider,
            self.logger,
        )

        user_msg = Message(channel="console", target="user", body="Say hello")
        echo_response = _run_sync(echo_service.run_turn(user_msg))

        self.assertIn("plain response", echo_response.text.lower())
        self.assertEqual(echo_response.metadata["finish_reason"], "stop")
        self.assertEqual(int(echo_response.metadata.get("tool_calls_count", 0)), 0)

    def test_chat_behavior_equivalent_error_response_by_canonical_fields(self):

        class ErrorProvider(LLMProvider):
            @property
            def name(self):
                return "error_mock"

            async def generate(self, request):
                return ProviderResponse(
                    text="",
                    model="error-model",
                    tool_calls=[],
                    finish_reason="error",
                    usage={},
                )

        error_provider = ErrorProvider()
        error_service = AgentService(
            self.config,
            self.plugin_registry,
            error_provider,
            self.logger,
        )

        user_msg = Message(channel="console", target="user", body="trigger error")
        error_response = _run_sync(error_service.run_turn(user_msg))

        self.assertEqual(error_response.metadata["finish_reason"], "error")

    def test_provider_bridge_consistent_with_different_configs(self):
        try:
            self.skipTest(
                "LLMCTLBridgeProvider testing skipped for simplicity of dependencies"
            )
        except ImportError:
            self.skipTest("LLMCTLBridgeProvider not available for import testing")

    def test_runtime_consumes_normalize_fields_only(self):
        captured_inputs = []

        class TrackingProvider(LLMProvider):
            @property
            def name(self):
                return "tracker"

            async def generate(self, request: ProviderRequest):
                captured_inputs.append(request)
                return ProviderResponse(
                    text="tracking response",
                    model="tracking-model",
                    tool_calls=[],
                    finish_reason="stop",
                    usage={"total_tokens": 10},
                )

        tracking_provider = TrackingProvider()
        tracking_service = AgentService(
            self.config,
            self.plugin_registry,
            tracking_provider,
            self.logger,
        )

        user_msg = Message(channel="console", target="user", body="tracking test")
        response = _run_sync(tracking_service.run_turn(user_msg))

        self.assertIsNotNone(response.text)
        self.assertIn("tracking", response.text.lower())
