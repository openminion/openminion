from __future__ import annotations

import asyncio
import logging
import unittest
from types import SimpleNamespace

from openminion.base.config import OpenMinionConfig
from openminion.modules.llm.providers.base import (
    ProviderError,
    ProviderRequest,
    ProviderToolSpec,
)
from openminion.services.agent import AgentService
from openminion.services.runtime.plugins import PluginRegistry


class _FakeUsage:
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0


class _FakeToolCall:
    def __init__(
        self, *, name: str, arguments: dict[str, object], status: str = "requested"
    ):
        self.id = ""
        self.name = name
        self.arguments = arguments
        self.status = status


class _FakeOkResponse:
    def __init__(
        self,
        *,
        tool_calls: list[_FakeToolCall] | None = None,
        output_text: str = "",
        finish_reason: str | None = None,
        usage: object | None = None,
    ) -> None:
        self.ok = True
        self.error = None
        self.output_text = output_text
        self.model = "MiniMax-M2.5"
        self.usage = usage if usage is not None else _FakeUsage()
        self.tool_calls = list(tool_calls or [])
        self.finish_reason = (
            finish_reason
            if finish_reason is not None
            else "tool_calls"
            if tool_calls
            else "stop"
        )
        self.contract_version = "v1"


class _FakeErrorResponse:
    def __init__(self, *, message: str) -> None:
        self.ok = False
        self.error = SimpleNamespace(code="PROVIDER_ERROR", message=message)
        self.output_text = ""
        self.model = "MiniMax-M2.5"
        self.usage = _FakeUsage()
        self.tool_calls = []
        self.finish_reason = ""
        self.contract_version = "v1"


class _CapturingRuntimeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def complete(self, **kwargs):
        self.calls.append(dict(kwargs))
        return _FakeOkResponse()


class _UsageDictRuntimeClient:
    def complete(self, **kwargs):
        del kwargs
        return _FakeOkResponse(
            output_text="ok",
            finish_reason="stop",
            usage={
                "prompt_tokens": 123,
                "completion_tokens": 45,
                "total_tokens": 168,
            },
        )


class _MinimaxXmlLeakRuntimeClient:
    def complete(self, **kwargs):
        del kwargs
        return _FakeOkResponse(
            output_text=(
                "<minimax:tool_call>\n"
                '  <invoke name="web.search">\n'
                '    <parameter name="query">latest Iran news</parameter>\n'
                "  </invoke>\n"
                "</minimax:tool_call>"
            )
        )


class _RejectedMinimaxXmlLeakRuntimeClient:
    def complete(self, **kwargs):
        del kwargs
        return _FakeOkResponse(
            output_text=(
                "<minimax:tool_call>\n"
                '  <invoke name="search_anything">\n'
                '    <parameter name="keywords">["latest Iran news"]</parameter>\n'
                "  </invoke>\n"
                "</minimax:tool_call>"
            )
        )


class _RetryingRuntimeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def complete(self, **kwargs):
        self.calls.append(dict(kwargs))
        if len(self.calls) == 1:
            return _FakeErrorResponse(
                message=(
                    "openai request failed with HTTP 400: "
                    "The tool_choice parameter does not support being set to required "
                    "or object in thinking mode"
                )
            )
        return _FakeOkResponse(
            tool_calls=[
                _FakeToolCall(
                    name="submit_output",
                    arguments={
                        "mode": "respond",
                        "confidence": 1.0,
                        "reason_code": "greeting",
                        "sub_intents": [],
                        "rationale": "",
                        "answer": "hello",
                    },
                    status="parsed",
                )
            ]
        )


def _service(*, client) -> AgentService:
    runtime = SimpleNamespace(client=client, name="openai", model="MiniMax-M2.5")
    return AgentService(
        OpenMinionConfig(),
        PluginRegistry([]),
        None,
        logging.getLogger("openminion.tests.runtime_client_structured"),
        llm_runtime=runtime,
    )


def _structured_request(*, metadata: dict[str, str] | None = None) -> ProviderRequest:
    return ProviderRequest(
        user_message="route this",
        system_prompt="You are helpful.",
        thinking="minimal",
        tools=[
            ProviderToolSpec(
                name="submit_output",
                description="Return structured output.",
                parameters={"type": "object"},
            )
        ],
        tool_choice={"type": "function", "function": {"name": "submit_output"}},
        metadata={"purpose": "decide", **dict(metadata or {})},
    )


class RuntimeClientStructuredToolChoiceTests(unittest.TestCase):
    def test_runtime_client_preserves_function_targeted_tool_choice_dict(self) -> None:
        client = _CapturingRuntimeClient()
        service = _service(client=client)

        response = asyncio.run(service._invoke_provider_request(_structured_request()))

        self.assertEqual(response.normalization.get("adapter"), "llm_runtime_client")
        self.assertEqual(len(client.calls), 1)
        self.assertIsInstance(client.calls[0]["tool_choice"], dict)
        self.assertEqual(
            client.calls[0]["tool_choice"],
            {"type": "function", "function": {"name": "submit_output"}},
        )

    def test_runtime_client_preserves_openai_style_usage_dict(self) -> None:
        client = _UsageDictRuntimeClient()
        service = _service(client=client)

        response = asyncio.run(service._invoke_provider_request(_structured_request()))

        self.assertEqual(
            response.usage,
            {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168},
        )

    def test_runtime_client_retries_with_shared_override_owner(self) -> None:
        client = _RetryingRuntimeClient()
        service = _service(client=client)

        response = asyncio.run(service._invoke_provider_request(_structured_request()))

        self.assertEqual(
            [call["tool_choice"] for call in client.calls],
            [
                {"type": "function", "function": {"name": "submit_output"}},
                "auto",
            ],
        )
        self.assertEqual(
            response.normalization.get("provider_retry_override"),
            "openai_structured_thinking_tool_choice_retry",
        )
        self.assertEqual(response.tool_calls[0].name, "submit_output")

    def test_runtime_client_retry_override_can_be_disabled_via_metadata(self) -> None:
        client = _RetryingRuntimeClient()
        service = _service(client=client)

        with self.assertRaisesRegex(ProviderError, "PROVIDER_ERROR"):
            asyncio.run(
                service._invoke_provider_request(
                    _structured_request(metadata={"provider_override_mode": "disabled"})
                )
            )

        self.assertEqual(len(client.calls), 1)

    def test_runtime_client_sanitizes_minimax_xml_tool_call_from_output_text(
        self,
    ) -> None:
        service = _service(client=_MinimaxXmlLeakRuntimeClient())

        weather_request = ProviderRequest(
            user_message="latest weather",
            system_prompt="You are helpful.",
            tools=[
                ProviderToolSpec(
                    name="web.search",
                    description="Search the web.",
                    parameters={"type": "object"},
                )
            ],
        )
        response = asyncio.run(service._invoke_provider_request(weather_request))
        self.assertEqual(response.tool_calls, [])
        self.assertTrue(
            response.text.startswith("[system: UNEXECUTABLE_TOOL_ENVELOPE]")
        )
        self.assertIn("Reason: unparseable", response.text)

    def test_runtime_client_sanitizes_unexecutable_minimax_xml_markup(self) -> None:
        service = _service(client=_RejectedMinimaxXmlLeakRuntimeClient())
        weather_request = ProviderRequest(
            user_message="latest weather",
            system_prompt="You are helpful.",
            tools=[
                ProviderToolSpec(
                    name="web.search",
                    description="Search the web.",
                    parameters={"type": "object"},
                )
            ],
        )

        response = asyncio.run(service._invoke_provider_request(weather_request))

        self.assertEqual(response.tool_calls, [])
        self.assertTrue(
            response.text.startswith("[system: UNEXECUTABLE_TOOL_ENVELOPE]")
        )
        self.assertEqual(response.normalization.get("envelope_sanitized"), True)
