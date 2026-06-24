import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.modules.llm.schemas import LLMResponse, UsageInfo
from openminion.modules.llm.providers.transport.trace import (
    trace_http_json_request,
    trace_http_json_response,
)
from openminion.services.brain.client import OpenMinionLLMClient


class FakeProvider:
    name = "fake-provider"

    def __init__(self) -> None:
        self.last_request = None

    async def generate(self, req):
        self.last_request = req
        return {
            "text": "ok",
            "model": "fake-model",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "tool_calls": [],
            "finish_reason": "stop",
        }

    async def agenerate(self, req):
        return await self.generate(req)


class FailingProvider(FakeProvider):
    async def generate(self, req):
        self.last_request = req
        raise RuntimeError("provider boom")


class ThinkingProvider(FakeProvider):
    async def generate(self, req):
        self.last_request = req
        return {
            "text": "ok",
            "model": "fake-model",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "tool_calls": [],
            "thinking": [{"type": "thinking", "content": "use the tool result"}],
            "finish_reason": "stop",
        }


class FakeTelemetry:
    def __init__(self) -> None:
        self.llm_calls: list[dict[str, object]] = []

    async def emit_llm_call(
        self,
        session_id: str,
        turn_id: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        mode: str | None = None,
    ) -> None:
        self.llm_calls.append(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_tokens": cached_tokens,
                "mode": mode,
            }
        )


class StructuredFieldProvider(FakeProvider):
    async def generate(self, req):
        self.last_request = req
        return LLMResponse(
            ok=True,
            provider=self.name,
            model="fake-model",
            output_text="done",
            finish_reason="stop",
            usage=UsageInfo(input_tokens=1, output_tokens=1, total_tokens=2),
            finalization_status={
                "status": "final_answer",
                "reasoning": "complete",
                "remaining_work": "",
                "blocking_reason": "",
            },
            pending_turn_context={
                "original_user_request": "continue later",
                "active_work_summary": "summary",
                "known_context": {"k": "v"},
                "missing_fields": [],
                "artifact_refs": [],
                "response_preferences": {},
            },
        )


def _request(*, purpose: str = "decide") -> SimpleNamespace:
    return SimpleNamespace(
        messages=[
            SimpleNamespace(role="system", content="sys"),
            SimpleNamespace(role="user", content="hello"),
        ],
        tools=[],
        metadata={"purpose": purpose},
        model="fake-model",
    )


def _schema_only_request() -> SimpleNamespace:
    return SimpleNamespace(
        messages=[
            SimpleNamespace(role="system", content="sys with session summary"),
            SimpleNamespace(role="user", content="hi"),
            SimpleNamespace(
                role="assistant", content="Hi there! How can I help you today?"
            ),
            SimpleNamespace(role="user", content="what's the weather in Tokyo?"),
        ],
        tools=[
            SimpleNamespace(
                name="submit_output",
                description="Submit structured output",
                input_schema={"type": "object"},
            )
        ],
        metadata={"purpose": "decide"},
        tool_choice={"type": "function", "function": {"name": "submit_output"}},
        model="fake-model",
    )


def test_llm_wrapper_keeps_decide_schema_only_without_runtime_tools() -> None:
    provider = FakeProvider()
    runtime_tool = SimpleNamespace(
        name="tool.one",
        description="desc",
        parameters={},
        strict=True,
    )
    wrapper = OpenMinionLLMClient(provider, runtime_tools=[runtime_tool])
    req = _request()

    response = wrapper.call(req)

    assert response.output_text == "ok"
    assert provider.last_request is not None
    assert provider.last_request.system_prompt == "sys"
    assert provider.last_request.user_message == "hello"
    assert provider.last_request.tools == []


def test_llm_wrapper_schema_only_submit_output_keeps_bounded_decide_history() -> None:
    provider = FakeProvider()
    wrapper = OpenMinionLLMClient(provider)

    response = wrapper.call(_schema_only_request())

    assert response.output_text == "ok"
    assert provider.last_request is not None
    assert provider.last_request.user_message == "what's the weather in Tokyo?"
    assert [item.role for item in provider.last_request.history] == [
        "user",
        "assistant",
    ]
    assert [item.content for item in provider.last_request.history] == [
        "hi",
        "Hi there! How can I help you today?",
    ]
    assert len(provider.last_request.tools) == 1
    assert provider.last_request.tools[0].name == "submit_output"
    assert provider.last_request.tool_choice == {
        "type": "function",
        "function": {"name": "submit_output"},
    }


def test_llm_wrapper_schema_only_non_decide_still_omits_duplicate_history() -> None:
    provider = FakeProvider()
    wrapper = OpenMinionLLMClient(provider)
    req = _schema_only_request()
    req.metadata = {"purpose": "plan"}

    response = wrapper.call(req)

    assert response.output_text == "ok"
    assert provider.last_request is not None
    assert provider.last_request.history == []


def test_llm_wrapper_non_schema_history_is_preserved() -> None:
    provider = FakeProvider()
    wrapper = OpenMinionLLMClient(provider)
    req = SimpleNamespace(
        messages=[
            SimpleNamespace(role="system", content="sys"),
            SimpleNamespace(role="user", content="hi"),
            SimpleNamespace(role="assistant", content="hello"),
            SimpleNamespace(role="user", content="weather in tokyo"),
        ],
        tools=[
            SimpleNamespace(
                name="weather",
                description="Get weather",
                input_schema={"type": "object"},
            )
        ],
        metadata={"purpose": "act"},
        tool_choice="auto",
        model="fake-model",
    )

    wrapper.call(req)

    assert provider.last_request is not None
    assert [item.role for item in provider.last_request.history] == [
        "user",
        "assistant",
    ]
    assert [item.content for item in provider.last_request.history] == ["hi", "hello"]


def test_llm_wrapper_traces_requests_and_responses_under_home_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    monkeypatch.delenv("OPENMINION_TRACE_REQUESTS_DIR", raising=False)
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    provider = FakeProvider()
    wrapper = OpenMinionLLMClient(provider, home_root=tmp_path)
    wrapper._set_context("sess-1", "turn-1")

    response = wrapper.call(_request(purpose="plan"))
    assert response.output_text == "ok"
    assert provider.last_request is not None
    assert provider.last_request.metadata is not None
    assert provider.last_request.metadata.get("session_id") == "sess-1"
    assert provider.last_request.metadata.get("turn_id") == "turn-1"
    assert provider.last_request.metadata.get("inference_step") == "1"
    assert provider.last_request.metadata.get("trace_label") == "call01"

    trace_root = (
        tmp_path / ".openminion" / "traces" / "llm" / "sess-1" / "turn-1-sess-1"
    )
    request_trace = trace_root / "step01-call01.json"
    response_trace = trace_root / "step01-call01-response.json"
    assert request_trace.exists()
    assert response_trace.exists()

    request_payload = json.loads(request_trace.read_text(encoding="utf-8"))
    response_payload = json.loads(response_trace.read_text(encoding="utf-8"))
    assert request_payload["provider"] == "fake-provider"
    assert request_payload["metadata"].get("__trace_home_root") is None
    assert request_payload["http_trace_filename"].endswith("/step01-call01-http.json")
    assert request_payload["http_response_trace_filename"].endswith(
        "/step01-call01-http-response.json"
    )
    assert request_payload["structured_trace_filename"].endswith(
        "/step01-call01-structured.json"
    )
    assert response_payload["provider"] == "fake-provider"
    assert response_payload["output_text"] == "ok"
    assert response_payload["http_trace_filename"].endswith("/step01-call01-http.json")
    assert response_payload["http_response_trace_filename"].endswith(
        "/step01-call01-http-response.json"
    )
    assert response_payload["structured_trace_filename"].endswith(
        "/step01-call01-structured.json"
    )


def test_llm_wrapper_traced_runtime_transport_uses_same_home_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    monkeypatch.delenv("OPENMINION_TRACE_REQUESTS_DIR", raising=False)
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    async def _invoke(req):
        body = {"model": "fake-model", "message_count": 1}
        trace_http_json_request(
            trace_metadata=dict(req.metadata or {}),
            provider_name="fake-provider",
            url="https://api.fake.test/v1/chat/completions",
            body_json=json.dumps(body),
            payload=body,
            headers={"Authorization": "Bearer test"},
            timeout_seconds=30,
            transport="urllib",
        )
        trace_http_json_response(
            trace_metadata=dict(req.metadata or {}),
            provider_name="fake-provider",
            url="https://api.fake.test/v1/chat/completions",
            status_code=200,
            body_text='{"ok":true}',
            transport="urllib",
            parsed_json={"ok": True},
        )
        return {
            "text": "ok",
            "model": "fake-model",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "tool_calls": [],
            "finish_reason": "stop",
        }

    wrapper = OpenMinionLLMClient(
        FakeProvider(),
        invoke_provider_request=_invoke,
        home_root=tmp_path,
    )
    wrapper._set_context("sess-transport", "turn-transport")
    req = _request(purpose="plan")
    req.metadata.update({"trace_id": "trace-123", "agent_id": "agent-xyz"})

    response = wrapper.call(req)

    assert response.output_text == "ok"
    trace_root = (
        tmp_path
        / ".openminion"
        / "traces"
        / "llm"
        / "sess-transport"
        / "turn-transport-sess-transport"
    )
    assert (trace_root / "step01-call01.json").exists()
    assert (trace_root / "step01-call01-response.json").exists()
    assert (trace_root / "step01-call01-structured.json").exists()
    assert (trace_root / "step01-call01-http.json").exists()
    assert (trace_root / "step01-call01-http-response.json").exists()

    request_payload = json.loads(
        (trace_root / "step01-call01.json").read_text(encoding="utf-8")
    )
    response_payload = json.loads(
        (trace_root / "step01-call01-response.json").read_text(encoding="utf-8")
    )
    structured_payload = json.loads(
        (trace_root / "step01-call01-structured.json").read_text(encoding="utf-8")
    )
    assert request_payload["trace"]["trace_id"] == "trace-123"
    assert request_payload["trace"]["agent_id"] == "agent-xyz"
    assert response_payload["trace"]["trace_id"] == "trace-123"
    assert response_payload["trace"]["agent_id"] == "agent-xyz"
    assert structured_payload["trace"]["trace_id"] == "trace-123"
    assert structured_payload["trace"]["agent_id"] == "agent-xyz"


def test_llm_wrapper_traces_provider_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    monkeypatch.delenv("OPENMINION_TRACE_REQUESTS_DIR", raising=False)
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    provider = FailingProvider()
    wrapper = OpenMinionLLMClient(provider, home_root=tmp_path)
    wrapper._set_context("sess-err", "turn-err")

    with pytest.raises(RuntimeError, match="provider boom"):
        wrapper.call(_request(purpose="decide"))
    assert provider.last_request is not None
    assert provider.last_request.metadata is not None
    assert provider.last_request.metadata.get("session_id") == "sess-err"
    assert provider.last_request.metadata.get("turn_id") == "turn-err"
    assert provider.last_request.metadata.get("inference_step") == "1"
    assert provider.last_request.metadata.get("trace_label") == "call01"

    trace_root = (
        tmp_path / ".openminion" / "traces" / "llm" / "sess-err" / "turn-err-sess-err"
    )
    request_trace = trace_root / "step01-call01.json"
    response_trace = trace_root / "step01-call01-response.json"
    assert request_trace.exists()
    assert response_trace.exists()

    response_payload = json.loads(response_trace.read_text(encoding="utf-8"))
    assert response_payload["ok"] is False
    assert "provider boom" in str(response_payload["error"])
    assert response_payload["http_trace_filename"].endswith("/step01-call01-http.json")
    assert response_payload["http_response_trace_filename"].endswith(
        "/step01-call01-http-response.json"
    )
    assert response_payload["structured_trace_filename"].endswith(
        "/step01-call01-structured.json"
    )


def test_llm_wrapper_trace_request_preserves_structured_tool_choice() -> None:
    provider = FakeProvider()
    wrapper = OpenMinionLLMClient(provider)

    wrapper.call(_schema_only_request())

    assert provider.last_request is not None
    assert provider.last_request.tool_choice == {
        "type": "function",
        "function": {"name": "submit_output"},
    }


def test_llm_wrapper_request_trace_preserves_structured_tool_choice(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    monkeypatch.delenv("OPENMINION_TRACE_REQUESTS_DIR", raising=False)
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    provider = FakeProvider()
    wrapper = OpenMinionLLMClient(provider, home_root=tmp_path)
    wrapper._set_context("sess-structured", "turn-structured")

    response = wrapper.call(_schema_only_request())

    assert response.output_text == "ok"
    request_trace = (
        tmp_path
        / ".openminion"
        / "traces"
        / "llm"
        / "sess-structured"
        / "turn-structured-sess-structured"
        / "step01-call01.json"
    )
    payload = json.loads(request_trace.read_text(encoding="utf-8"))
    assert payload["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_output"},
    }


def test_llm_wrapper_writes_thinking_blocks_into_traces(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    monkeypatch.delenv("OPENMINION_TRACE_REQUESTS_DIR", raising=False)
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    provider = ThinkingProvider()
    wrapper = OpenMinionLLMClient(provider, home_root=tmp_path)
    wrapper._set_context("sess-think", "turn-think")

    response = wrapper.call(_request())

    assert len(response.thinking) == 1
    trace_root = (
        tmp_path
        / ".openminion"
        / "traces"
        / "llm"
        / "sess-think"
        / "turn-think-sess-think"
    )
    response_trace = trace_root / "step01-call01-response.json"
    structured_trace = trace_root / "step01-call01-structured.json"
    response_payload = json.loads(response_trace.read_text(encoding="utf-8"))
    structured_payload = json.loads(structured_trace.read_text(encoding="utf-8"))
    assert response_payload["thinking_blocks"][0]["content"] == "use the tool result"
    assert (
        structured_payload["response"]["thinking_blocks"][0]["content"]
        == "use the tool result"
    )


def test_llm_wrapper_emits_llm_call_mode_from_request_metadata() -> None:
    provider = FakeProvider()
    telemetry = FakeTelemetry()
    wrapper = OpenMinionLLMClient(provider, telemetryctl=telemetry)
    wrapper._set_context("sess-1", "turn-1")
    req = _request(purpose="plan")
    req.metadata["mode_name"] = "plan"

    response = wrapper.call(req)

    assert response.output_text == "ok"
    assert telemetry.llm_calls == [
        {
            "session_id": "sess-1",
            "turn_id": "turn-1",
            "input_tokens": 1,
            "output_tokens": 1,
            "cached_tokens": 0,
            "mode": "plan",
        }
    ]


def test_llm_wrapper_preserves_structured_response_fields_from_upstream_llm_response() -> (
    None
):
    provider = StructuredFieldProvider()
    wrapper = OpenMinionLLMClient(provider)

    response = wrapper.call(_request(purpose="act"))

    assert response.output_text == "done"
    assert response.finalization_status == {
        "status": "final_answer",
        "reasoning": "complete",
        "remaining_work": "",
        "blocking_reason": "",
    }
    assert response.pending_turn_context is not None
    assert response.pending_turn_context["active_work_summary"] == "summary"
    assert response.usage.input_tokens == 1
    assert response.usage.output_tokens == 1
    assert response.usage.total_tokens == 2


def test_llm_wrapper_emits_telemetry_from_typed_usage_info() -> None:
    provider = StructuredFieldProvider()
    telemetry = FakeTelemetry()
    wrapper = OpenMinionLLMClient(provider, telemetryctl=telemetry)
    wrapper._set_context("sess-typed", "turn-typed")
    req = _request(purpose="act")
    req.metadata["mode_name"] = "act"

    response = wrapper.call(req)

    assert response.usage.input_tokens == 1
    assert response.usage.output_tokens == 1
    assert telemetry.llm_calls == [
        {
            "session_id": "sess-typed",
            "turn_id": "turn-typed",
            "input_tokens": 1,
            "output_tokens": 1,
            "cached_tokens": 0,
            "mode": "act",
        }
    ]
