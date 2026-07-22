from __future__ import annotations

from types import SimpleNamespace

from tests.brain.runner_test_support import (
    fake_bridge_api,
    fake_bridge_llm_adapter,
    fake_model_dump_message,
)


# Helpers


def _make_llm_response(
    *, ok: bool = True, text: str = "hello", error_msg: str = "fail"
):
    from openminion.modules.llm.schemas import LLMResponse, UsageInfo

    if ok:
        return LLMResponse(
            ok=True,
            provider="mock",
            model="mock-model",
            output_text=text,
            usage=UsageInfo(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                total_source="derived",
                cached_tokens=4,
                cache_creation_tokens=2,
            ),
        )
    from openminion.modules.llm.schemas import ResponseError

    return LLMResponse(
        ok=False,
        provider="mock",
        model="mock-model",
        error=ResponseError(code="PROVIDER_ERROR", message=error_msg),
    )


def _make_llm_adapter(response):
    return fake_bridge_llm_adapter(response=response)


# RLMBridgeLLMClient tests


class TestRLMBridgeLLMClient:
    def test_successful_call(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeLLMClient

        resp = _make_llm_response(ok=True, text="Generated answer")
        adapter = _make_llm_adapter(resp)
        bridge = RLMBridgeLLMClient(adapter)

        result = bridge.call_for_agent(
            agent_id="test-agent",
            purpose="generate",
            request={
                "messages": [{"role": "user", "content": "What is 2+2?"}],
                "budget": {"max_tokens": 512},
            },
            agent_policy={},
        )

        assert result["status"] == "completed"
        assert result["text"] == "Generated answer"
        assert result["usage"]["input_tokens"] == 10
        assert result["usage"]["output_tokens"] == 5
        assert result["usage"]["total_source"] == "derived"
        assert result["usage"]["cached_tokens"] == 4
        assert result["usage"]["cache_creation_tokens"] == 2
        # Verify the underlying client.call was invoked with an LLMRequest
        adapter.client.call.assert_called_once()
        req_arg = adapter.client.call.call_args[0][0]
        assert len(req_arg.messages) == 1
        assert req_arg.messages[0].content == "What is 2+2?"

    def test_failed_llm_response(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeLLMClient

        resp = _make_llm_response(ok=False, error_msg="rate limit exceeded")
        adapter = _make_llm_adapter(resp)
        bridge = RLMBridgeLLMClient(adapter)

        result = bridge.call_for_agent(
            agent_id="test-agent",
            purpose="generate",
            request={"messages": [{"role": "user", "content": "hi"}]},
        )

        assert result["status"] == "error"
        assert "rate limit" in result["text"]

    def test_exception_returns_error(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeLLMClient

        adapter = fake_bridge_llm_adapter(side_effect=ConnectionError("network down"))
        bridge = RLMBridgeLLMClient(adapter)

        result = bridge.call_for_agent(
            agent_id="test-agent",
            purpose="generate",
            request={"messages": [{"role": "user", "content": "hi"}]},
        )

        assert result["status"] == "error"
        assert "network down" in result["text"]

    def test_empty_messages(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeLLMClient

        resp = _make_llm_response(ok=True, text="ok")
        adapter = _make_llm_adapter(resp)
        bridge = RLMBridgeLLMClient(adapter)

        result = bridge.call_for_agent(
            agent_id="test-agent",
            purpose="generate",
            request={},
        )

        assert result["status"] == "completed"
        req_arg = adapter.client.call.call_args[0][0]
        assert len(req_arg.messages) == 0

    def test_tool_calls_in_response(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeLLMClient
        from openminion.modules.llm.schemas import LLMResponse, ToolCall, UsageInfo

        resp = LLMResponse(
            ok=True,
            provider="mock",
            model="mock-model",
            output_text="",
            tool_calls=[
                ToolCall(name="weather.openmeteo.current", arguments={"city": "NYC"})
            ],
            usage=UsageInfo(input_tokens=5, output_tokens=3),
        )
        adapter = _make_llm_adapter(resp)
        # Override the call return
        adapter.client.call.return_value = resp
        bridge = RLMBridgeLLMClient(adapter)

        result = bridge.call_for_agent(
            agent_id="test-agent",
            purpose="generate",
            request={"messages": [{"role": "user", "content": "weather?"}]},
        )

        assert result["status"] == "completed"
        assert result["json_output"] is not None
        assert result["json_output"][0]["name"] == "weather.openmeteo.current"


# RLMBridgeSessionClient tests


class TestRLMBridgeSessionClient:
    def test_get_latest_working_state(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeSessionClient

        mock_api = fake_bridge_api(
            return_values={"get_latest_working_state": {"plan": "test"}}
        )
        bridge = RLMBridgeSessionClient(mock_api)

        result = bridge.get_latest_working_state("sess-1")
        assert result == {"plan": "test"}
        mock_api.get_latest_working_state.assert_called_once_with("sess-1")

    def test_put_working_state(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeSessionClient

        mock_api = fake_bridge_api(return_values={"put_working_state": 42})
        bridge = RLMBridgeSessionClient(mock_api)

        result = bridge.put_working_state("sess-1", state_inline={"key": "val"})
        assert result == 42

    def test_append_event(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeSessionClient

        mock_api = fake_bridge_api(return_values={"append_event": "evt-123"})
        bridge = RLMBridgeSessionClient(mock_api)

        result = bridge.append_event("sess-1", type="rlm.tick", payload={"tick": 1})
        assert result == "evt-123"

    def test_list_events_with_filter_fallback(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeSessionClient

        # No list_events on store → falls back to api.list_events + filter
        mock_api = fake_bridge_api(
            return_values={
                "list_events": [
                    {"type": "rlm.tick", "seq": 1},
                    {"type": "user.input", "seq": 2},
                    {"type": "rlm.tick", "seq": 3},
                ]
            },
            store=SimpleNamespace(),
        )
        bridge = RLMBridgeSessionClient(mock_api)

        result = bridge.list_events("sess-1", event_type="rlm.tick")
        assert len(result) == 2
        assert all(e["type"] == "rlm.tick" for e in result)

    def test_get_slice_delegates(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeSessionClient

        mock_api = fake_bridge_api(
            return_values={
                "get_slice": {"messages": [{"role": "user", "content": "hi"}]}
            },
            # Ensure store doesn't have get_slice so bridge falls through to get_slice.
            store=SimpleNamespace(other=lambda: None),
        )
        bridge = RLMBridgeSessionClient(mock_api)

        result = bridge.get_slice("sess-1", "chat", {})
        assert "messages" in result


# RLMBridgeContextClient tests


class TestRLMBridgeContextClient:
    def test_build_pack_success(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeContextClient

        mock_api = fake_bridge_api(
            return_values={
                "build_pack": [
                    fake_model_dump_message({"role": "system", "content": "ctx"})
                ]
            }
        )
        bridge = RLMBridgeContextClient(mock_api)

        result = bridge.build_pack(
            {
                "session_id": "s1",
                "purpose": "chat",
                "agent_id": "a1",
                "query": "test query",
            }
        )
        assert "messages" in result
        assert len(result["messages"]) == 1

    def test_build_pack_error_returns_empty(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeContextClient

        mock_api = fake_bridge_api(
            side_effects={"build_pack": ValueError("bad request")}
        )
        bridge = RLMBridgeContextClient(mock_api)

        result = bridge.build_pack({})
        assert result == {"messages": []}


# RLMBridgeArtifactClient tests (BMC-10 characterization)


class TestRLMBridgeArtifactClient:
    def test_ingest_delegates(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeArtifactClient

        mock_api = fake_bridge_api(return_values={"ingest": "art-001"})
        bridge = RLMBridgeArtifactClient(mock_api)

        result = bridge.ingest("sess-1", "hello world", "text/plain", {"key": "val"})
        assert result == "art-001"
        mock_api.ingest.assert_called_once_with(
            session_id="sess-1",
            content="hello world",
            mime_type="text/plain",
            metadata={"key": "val"},
        )

    def test_ingest_fallback_when_api_lacks_method(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeArtifactClient

        mock_api = fake_bridge_api()
        del mock_api.ingest
        bridge = RLMBridgeArtifactClient(mock_api)

        result = bridge.ingest("sess-1", "content")
        assert result == ""

    def test_get_delegates(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeArtifactClient

        mock_api = fake_bridge_api(
            return_values={"get": {"id": "art-001", "content": "data"}}
        )
        bridge = RLMBridgeArtifactClient(mock_api)

        result = bridge.get("art-001")
        assert result == {"id": "art-001", "content": "data"}

    def test_get_fallback_when_api_lacks_method(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeArtifactClient

        mock_api = fake_bridge_api()
        del mock_api.get
        bridge = RLMBridgeArtifactClient(mock_api)

        result = bridge.get("art-001")
        assert result is None

    def test_list_delegates(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeArtifactClient

        mock_api = fake_bridge_api(return_values={"list": [{"id": "a1"}, {"id": "a2"}]})
        bridge = RLMBridgeArtifactClient(mock_api)

        result = bridge.list("sess-1", limit=10)
        assert len(result) == 2

    def test_list_fallback_when_api_lacks_method(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeArtifactClient

        mock_api = fake_bridge_api()
        del mock_api.list
        bridge = RLMBridgeArtifactClient(mock_api)

        result = bridge.list("sess-1")
        assert result == []


# RLMBridgeSkillClient tests (BMC-10 characterization)


class TestRLMBridgeSkillClient:
    def test_search_delegates(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeSkillClient

        mock_api = fake_bridge_api(
            return_values={"search": [{"id": "sk-1", "score": 0.9}]}
        )
        bridge = RLMBridgeSkillClient(mock_api)

        result = bridge.search("weather", limit=3)
        assert len(result) == 1
        mock_api.search.assert_called_once_with(query="weather", limit=3)

    def test_search_fallback_when_api_lacks_method(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeSkillClient

        mock_api = fake_bridge_api()
        del mock_api.search
        bridge = RLMBridgeSkillClient(mock_api)

        result = bridge.search("query")
        assert result == []

    def test_get_delegates(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeSkillClient

        mock_api = fake_bridge_api(
            return_values={"get": {"id": "sk-1", "name": "weather"}}
        )
        bridge = RLMBridgeSkillClient(mock_api)

        result = bridge.get("sk-1")
        assert result == {"id": "sk-1", "name": "weather"}

    def test_get_fallback_when_api_lacks_method(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeSkillClient

        mock_api = fake_bridge_api()
        del mock_api.get
        bridge = RLMBridgeSkillClient(mock_api)

        result = bridge.get("sk-1")
        assert result is None


# RLMBridgeCompressClient tests (BMC-10 characterization)


class TestRLMBridgeCompressClient:
    def test_compress_delegates(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeCompressClient

        mock_api = fake_bridge_api(return_values={"compress": "compressed text"})
        bridge = RLMBridgeCompressClient(mock_api)

        result = bridge.compress("long text here", max_tokens=500)
        assert result == "compressed text"
        mock_api.compress.assert_called_once_with(text="long text here", max_tokens=500)

    def test_compress_fallback_returns_original(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeCompressClient

        mock_api = fake_bridge_api()
        del mock_api.compress
        bridge = RLMBridgeCompressClient(mock_api)

        result = bridge.compress("original text")
        assert result == "original text"

    def test_extract_delegates(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeCompressClient

        mock_api = fake_bridge_api(
            return_values={"extract": ["block1", "block2", "block3"]}
        )
        bridge = RLMBridgeCompressClient(mock_api)

        result = bridge.extract("long text", num_blocks=3)
        assert result == ["block1", "block2", "block3"]

    def test_extract_fallback_returns_whole_text(self):
        from openminion.modules.brain.adapters.factory import RLMBridgeCompressClient

        mock_api = fake_bridge_api()
        del mock_api.extract
        bridge = RLMBridgeCompressClient(mock_api)

        result = bridge.extract("whole text")
        assert result == ["whole text"]
