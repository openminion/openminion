from __future__ import annotations

import asyncio
from typing import Any, Dict, Iterator, List
from unittest.mock import patch

from openminion.modules.llm import LLMCTL
from openminion.modules.llm.contracts.adapter import (
    ProviderAdapterResult,
    adapter_result_to_llm_response,
    coerce_provider_output,
)
from openminion.modules.llm.interfaces import LLM_RESPONSE_INTERFACE_VERSION
from openminion.modules.llm.schemas import (
    LLMRequest,
    LLMStreamEvent,
    ToolCall,
    UsageInfo,
)


class _AdapterOnlyProvider:
    name = "adapter_only"
    contract_version = LLM_RESPONSE_INTERFACE_VERSION

    def __init__(self) -> None:
        self.calls = 0
        self.last_request: LLMRequest | None = None

    def complete(
        self, request: LLMRequest, config: Dict[str, Any]
    ) -> ProviderAdapterResult:
        del config
        self.calls += 1
        self.last_request = request
        return ProviderAdapterResult(
            provider=self.name,
            model=request.model or "adapter-only-v1",
            output_text='{"ok":true}',
            assistant_messages=[],
            tool_calls=[
                ToolCall(name="weather.openmeteo.current", arguments={"city": "Tokyo"})
            ],
            usage=UsageInfo(input_tokens=5, output_tokens=3, total_tokens=8),
            finish_reason="tool_calls",
            normalization_meta={"adapter": "test"},
        )

    def stream(
        self, request: LLMRequest, config: Dict[str, Any]
    ) -> Iterator[LLMStreamEvent]:
        del request, config
        return iter(())

    def list_models(self, config: Dict[str, Any]) -> List[str]:
        del config
        return ["adapter-only-v1"]

    def healthcheck(self, config: Dict[str, Any]) -> Dict[str, Any]:
        del config
        return {"ok": True}


def test_adapter_result_to_llm_response_infers_assistant_message() -> None:
    result = ProviderAdapterResult(
        provider="adapter",
        model="v1",
        output_text="hello",
        assistant_messages=[],
        tool_calls=[],
        usage=UsageInfo(input_tokens=1, output_tokens=1, total_tokens=2),
        finish_reason="stop",
    )
    response = adapter_result_to_llm_response(result)
    assert response.ok is True
    assert response.output_text == "hello"
    assert len(response.assistant_messages) == 1
    assert response.assistant_messages[0].content == "hello"
    assert response.finish_reason == "stop"


def test_coerce_provider_output_accepts_adapter_result_dict_shape() -> None:
    payload = {
        "provider": "adapter",
        "model": "v1",
        "output_text": "ready",
        "assistant_messages": [],
        "tool_calls": [],
        "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        "latency_ms": 12,
        "finish_reason": "stop",
        "normalization_meta": {"adapter": "dict-fixture"},
    }
    response = coerce_provider_output(payload)
    assert response.ok is True
    assert response.provider == "adapter"
    assert response.model == "v1"
    assert response.finish_reason == "stop"
    assert response.usage.total_tokens == 5


def test_coerce_provider_output_promotes_cache_telemetry_from_provider_raw() -> None:
    payload = {
        "ok": True,
        "provider": "adapter",
        "model": "v1",
        "output_text": "ready",
        "assistant_messages": [],
        "tool_calls": [],
        "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        "provider_raw": {"usage": {"cached_tokens": 7}},
        "finish_reason": "stop",
    }
    response = coerce_provider_output(payload)
    assert response.ok is True
    assert response.telemetry == {"cache_hit": True, "cached_tokens": 7.0}


def test_llm_client_accepts_provider_adapter_contract_output() -> None:
    runtime = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {"default_provider": "stub", "default_model": "stub-v1"},
            "providers": {"adapter_only": {}},
            "agents": {
                "default": {
                    "default_provider": "adapter_only",
                    "default_model": "adapter-only-v1",
                    "tool_policy": {"enable_tools": True},
                }
            },
        }
    )
    runtime.registry.add(_AdapterOnlyProvider())

    client = runtime.client(agent_name="default")
    response = client.complete(
        messages=[{"role": "user", "content": "weather?"}],
        tools=[
            {
                "name": "weather.openmeteo.current",
                "description": "Lookup weather",
                "input_schema": {},
            }
        ],
    )
    assert response.ok is True
    assert response.provider == "adapter_only"
    assert response.finish_reason == "tool_calls"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "weather.openmeteo.current"


def test_llm_client_complete_coerces_stringified_function_tool_choice() -> None:
    runtime = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {"default_provider": "stub", "default_model": "stub-v1"},
            "providers": {"adapter_only": {}},
            "agents": {
                "default": {
                    "default_provider": "adapter_only",
                    "default_model": "adapter-only-v1",
                    "tool_policy": {"enable_tools": True},
                }
            },
        }
    )
    provider = _AdapterOnlyProvider()
    runtime.registry.add(provider)

    client = runtime.client(agent_name="default")
    response = client.complete(
        messages=[{"role": "user", "content": "route"}],
        tools=[
            {
                "name": "weather.openmeteo.current",
                "description": "Structured response",
                "input_schema": {},
            }
        ],
        tool_choice="{'type': 'function', 'function': {'name': 'weather.openmeteo.current'}}",
    )

    assert response.ok is True
    assert provider.last_request is not None
    assert provider.last_request.tool_choice == {
        "type": "function",
        "function": {"name": "weather.openmeteo.current"},
    }


def test_llm_client_call_is_async_awaitable() -> None:
    runtime = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {"default_provider": "stub", "default_model": "stub-v1"},
            "providers": {"adapter_only": {}},
            "agents": {
                "default": {
                    "default_provider": "adapter_only",
                    "default_model": "adapter-only-v1",
                    "tool_policy": {"enable_tools": True},
                }
            },
        }
    )
    runtime.registry.add(_AdapterOnlyProvider())
    client = runtime.client(agent_name="default")
    request_payload = {
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [
            {
                "name": "weather.openmeteo.current",
                "description": "Lookup weather",
                "input_schema": {},
            }
        ],
    }

    async def _run() -> Any:
        return await client.call(request_payload)

    response = asyncio.run(_run())
    assert response.ok is True
    assert response.provider == "adapter_only"


def test_llm_client_call_sync_is_safe_inside_running_loop() -> None:
    runtime = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {"default_provider": "stub", "default_model": "stub-v1"},
            "providers": {"adapter_only": {}},
            "agents": {
                "default": {
                    "default_provider": "adapter_only",
                    "default_model": "adapter-only-v1",
                    "tool_policy": {"enable_tools": True},
                }
            },
        }
    )
    runtime.registry.add(_AdapterOnlyProvider())
    client = runtime.client(agent_name="default")
    request_payload = {
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [
            {
                "name": "weather.openmeteo.current",
                "description": "Lookup weather",
                "input_schema": {},
            }
        ],
    }

    async def _run() -> Any:
        return client.call_sync(request_payload)

    response = asyncio.run(_run())
    assert response.ok is True
    assert response.provider == "adapter_only"


def test_llm_client_async_call_offloads_via_to_thread() -> None:
    runtime = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {"default_provider": "stub", "default_model": "stub-v1"},
            "providers": {"adapter_only": {}},
            "agents": {
                "default": {
                    "default_provider": "adapter_only",
                    "default_model": "adapter-only-v1",
                    "tool_policy": {"enable_tools": True},
                }
            },
        }
    )
    runtime.registry.add(_AdapterOnlyProvider())
    client = runtime.client(agent_name="default")
    request_payload = {
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [
            {
                "name": "weather.openmeteo.current",
                "description": "Lookup weather",
                "input_schema": {},
            }
        ],
    }
    seen: dict[str, bool] = {"called": False}

    async def _fake_to_thread(fn: Any, *args: Any, **kwargs: Any) -> Any:
        seen["called"] = True
        return fn(*args, **kwargs)

    with patch(
        "openminion.modules.llm.runtime.client.asyncio.to_thread",
        side_effect=_fake_to_thread,
    ):
        response = asyncio.run(client.call(request_payload))

    assert seen["called"] is True
    assert response.ok is True


def test_llm_client_rejects_stream_true_under_option_b_contract() -> None:
    runtime = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {"default_provider": "stub", "default_model": "stub-v1"},
            "providers": {"adapter_only": {}},
            "agents": {
                "default": {
                    "default_provider": "adapter_only",
                    "default_model": "adapter-only-v1",
                }
            },
        }
    )
    provider = _AdapterOnlyProvider()
    runtime.registry.add(provider)
    client = runtime.client(agent_name="default")

    response = client.call_sync(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        }
    )
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "INVALID_ARGUMENT"
    assert provider.calls == 0


def test_llm_client_call_sync_rejects_stream_true_inside_running_loop() -> None:
    runtime = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {"default_provider": "stub", "default_model": "stub-v1"},
            "providers": {"adapter_only": {}},
            "agents": {
                "default": {
                    "default_provider": "adapter_only",
                    "default_model": "adapter-only-v1",
                }
            },
        }
    )
    provider = _AdapterOnlyProvider()
    runtime.registry.add(provider)
    client = runtime.client(agent_name="default")

    async def _run() -> Any:
        return client.call_sync(
            {
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            }
        )

    response = asyncio.run(_run())
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "INVALID_ARGUMENT"
    assert provider.calls == 0
