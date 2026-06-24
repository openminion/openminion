from __future__ import annotations


from openminion.modules.llm.providers.base import (
    ProviderResponse,
    ProviderToolCall,
    ProviderRequest,
)
from openminion.modules.llm.providers.normalization import normalize_provider_response
from openminion.services.lifecycle.request_orchestrator import (
    _normalize_result_to_dict,
)
from openminion.modules.tool.base import ToolExecutionResult
from openminion.base.types import Message


class TestProviderResponseContract:
    def test_provider_response_schema_consistency(self) -> None:
        raw_response = {
            "text": "Hello, world!",
            "model": "test-model",
            "usage": {"input_tokens": 10, "output_tokens": 20},
            "tool_calls": [{"name": "web_search", "arguments": {"query": "test"}}],
            "finish_reason": "stop",
        }

        normalized = normalize_provider_response(
            raw_response, provider_name="test_provider", model_name="test_model"
        )
        assert isinstance(normalized, ProviderResponse)
        assert normalized.text == "Hello, world!"
        assert normalized.model == "test-model"
        assert isinstance(normalized.usage, dict)
        assert isinstance(normalized.tool_calls, list)
        assert isinstance(normalized.tool_calls[0], ProviderToolCall)
        assert normalized.tool_calls[0].name == "web_search"
        assert normalized.finish_reason == "stop"

    def test_tool_call_coercion_contract(self) -> None:
        raw_call = {
            "id": "call123",
            "name": "web_search",
            "arguments": {"query": "hello world"},
            "source": "native",
        }

        from openminion.modules.llm.providers.normalization import _coerce_tool_call

        coerced_call = _coerce_tool_call(raw_call)

        assert coerced_call is not None
        assert coerced_call.id == "call123"
        assert coerced_call.name == "web_search"
        assert isinstance(coerced_call.arguments, dict)
        assert coerced_call.arguments["query"] == "hello world"
        assert coerced_call.source == "native"

        raw_json_args = {
            "name": "web_search",
            "arguments": '{"query": "hello world", "max_results": 5}',
        }

        coerced_json = _coerce_tool_call(raw_json_args)
        assert coerced_json is not None
        assert coerced_json.arguments["query"] == "hello world"
        assert coerced_json.arguments["max_results"] == 5

    def test_provider_response_contract_positive_path(self) -> None:
        valid_response = ProviderResponse(
            text="Sample response",
            model="gpt-4o",
            usage={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            tool_calls=[
                ProviderToolCall(
                    name="web_search",
                    arguments={"query": "latest news"},
                    id="call_abc123",
                )
            ],
            finish_reason="tool_calls",
        )

        normalized = normalize_provider_response(valid_response)
        assert normalized.text == "Sample response"
        assert normalized.model == "gpt-4o"
        assert len(normalized.tool_calls) == 1
        assert normalized.tool_calls[0].name == "web_search"
        assert normalized.normalization["response_contract_version"] == "v1"
        assert normalized.normalization["response_normalized"]

    def test_provider_response_contract_negative_path(self) -> None:
        class BadResponse:
            pass

        from openminion.modules.llm.providers.normalization import ProviderError

        try:

            def create_invalid_response():
                class MockResponse:
                    wrong_attr = "wrong"
                    another_field = 123

                return MockResponse()

            invalid_resp = create_invalid_response()
            normalized = normalize_provider_response(
                invalid_resp, recover_empty_payload=True
            )
            assert normalized.text != ""
            assert "unknown-model" in (normalized.model, str(normalized.model)[:20])
        except ProviderError:
            pass


class TestMessagePayLoadContract:
    def test_message_payload_normalization_contract(self) -> None:
        original_msg = Message(
            channel="console",
            target="user",
            body="Sample response content",
            metadata={
                "model": "gpt-4o",
                "provider": "openai",
                "tool_calls": [
                    {"name": "web_search", "arguments": {"query": "weather"}}
                ],
            },
            id="msg-123",
        )

        result = _normalize_result_to_dict(original_msg)

        assert result["id"] == "msg-123"
        assert result["channel"] == "console"
        assert result["target"] == "user"
        assert result["body"] == "Sample response content"
        assert "metadata" in result
        assert isinstance(result["metadata"], dict)
        assert result["metadata"]["model"] == "gpt-4o"

    def test_message_normalization_different_formats(self) -> None:
        dict_msg = {
            "id": "dict-msg-456",
            "channel": "console",
            "target": "user",
            "body": "Hello from dict",
            "metadata": {"test": True},
        }

        result1 = _normalize_result_to_dict(dict_msg)
        assert result1["id"] == "dict-msg-456"
        assert result1["body"] == "Hello from dict"

        class DictLikeObj:
            def __init__(self):
                self.id = "obj-msg-789"
                self.channel = "api"
                self.target = "client"
                self.body = "Hello from object"
                self.metadata = {"test": "value"}

        obj_msg = DictLikeObj()
        result2 = _normalize_result_to_dict(obj_msg)
        assert result2["id"] == "obj-msg-789"
        assert result2["body"] == "Hello from object"


class TestToolCallEnvelopeContract:
    def test_tool_execution_result_schema(self) -> None:
        result = ToolExecutionResult(
            tool_name="web_search",
            ok=True,
            content="Search results for query",
            verified=False,
            error="",
            data={
                "query": "latest news",
                "results": ["result1", "result2"],
                "count": 2,
            },
            call_id="call-xyz",
            source="tavily",
        )

        assert result.tool_name == "web_search"
        assert result.ok is True
        assert result.content == "Search results for query"
        assert result.verified is False
        assert result.error == ""
        assert isinstance(result.data, dict)
        assert result.call_id == "call-xyz"
        assert result.source == "tavily"


class TestGatewayLoopBoundaryContract:
    def test_gateway_request_response_payload_contract(self) -> None:
        request = ProviderRequest(
            user_message="Hello!",
            system_prompt="You are a helpful bot.",
            thinking="minimal",
            history=[],
            tools=[],
            tool_choice="auto",
            tool_call_strategy="hybrid",
            metadata={"session_id": "test-123", "request_id": "req-456"},
        )

        assert hasattr(request, "user_message")
        assert hasattr(request, "system_prompt")
        assert hasattr(request, "thinking")
        assert hasattr(request, "history")
        assert hasattr(request, "tools")
        assert hasattr(request, "tool_choice")
        assert hasattr(request, "metadata")
        assert isinstance(request.metadata, dict)

        sim_response = ProviderResponse(
            text="Hello there!",
            model="test-model",
            usage={"input_tokens": 20, "output_tokens": 15},
            tool_calls=[],
            finish_reason="stop",
        )

        assert hasattr(sim_response, "text")
        assert hasattr(sim_response, "model")
        assert hasattr(sim_response, "usage")
        assert hasattr(sim_response, "tool_calls")
        assert hasattr(sim_response, "finish_reason")
        assert isinstance(sim_response.text, str)
        assert isinstance(sim_response.usage, dict)
        assert isinstance(sim_response.tool_calls, list)
