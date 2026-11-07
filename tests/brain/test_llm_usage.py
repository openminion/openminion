from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.diagnostics.usage import (
    llm_usage_scope,
    record_llm_response,
)
from openminion.modules.llm.schemas import LLMResponse, UsageInfo


def test_response_facts_preserve_missing_usage_and_do_not_repeat_entry(tmp_path):
    session = LocalSessionStore(tmp_path)
    response = LLMResponse(ok=True, provider="test", model="model")
    record_llm_response(purpose="reflect", response=response)
    assert session.list_events("s") == []
    with llm_usage_scope(session, "s", "trace", "run"):
        record_llm_response(purpose="reflect", response=response)
        record_llm_response(purpose="entry", response=response)
        record_llm_response(
            purpose="judge",
            response=response.model_copy(
                update={"usage": UsageInfo(input_tokens=0, output_tokens=3)}
            ),
        )
    events = session.list_events("s")
    assert len(events) == 2
    assert events[0]["payload"]["usage"] == {}
    assert events[1]["payload"]["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 3,
        "total_tokens": 3,
        "total_source": "derived",
    }
    assert all(event["payload"]["run_id"] == "run" for event in events)
    assert len({event["payload"]["llm_call_id"] for event in events}) == 2
    record_llm_response(purpose="reflect", response=response)
    assert len(session.list_events("s")) == 2


def test_usage_context_isolated_across_concurrent_and_nested_turns(tmp_path):
    session = LocalSessionStore(tmp_path)
    barrier = Barrier(2)
    response = LLMResponse(ok=True, provider="test", model="model")

    def turn(name):
        with llm_usage_scope(session, name, f"trace-{name}", f"run-{name}"):
            barrier.wait(timeout=5)
            record_llm_response(purpose="reflect", response=response)
            with (
                pytest.raises(RuntimeError),
                llm_usage_scope(session, f"child-{name}", "child", "child-run"),
            ):
                record_llm_response(purpose="act", response=response)
                raise RuntimeError("stop child")
            record_llm_response(purpose="judge", response=response)

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(turn, ("a", "b")))
    for name in ("a", "b"):
        events = session.list_events(name)
        assert [event["payload"]["purpose"] for event in events] == ["reflect", "judge"]
        assert {event["trace_id"] for event in events} == {f"trace-{name}"}
        assert {event["payload"]["run_id"] for event in events} == {f"run-{name}"}


def test_failed_response_retains_known_usage_and_unknown_purpose(tmp_path):
    from types import SimpleNamespace

    from openminion.services.brain.post_execution.usage import (
        collect_llm_call_events,
        count_llm_calls,
        summarize_llm_usage,
    )

    session = LocalSessionStore(tmp_path)
    with llm_usage_scope(session, "s", "trace", "run"):
        record_llm_response(
            purpose="custom_phase",
            response=LLMResponse(
                ok=False,
                provider="test",
                model="model",
                usage=UsageInfo(input_tokens=4, output_tokens=2),
            ),
        )
    events = collect_llm_call_events(
        runner=SimpleNamespace(session_api=session),
        session_id="s",
        trace_id="trace",
        run_id="run",
    )
    assert events[0]["type"] == "llm.call.failed"
    assert count_llm_calls(events, 9)["custom_phase"] == 1
    assert summarize_llm_usage(events)["total_tokens_used"] == 6
    assert count_llm_calls([], 9)["unknown"] == 9


def _act_messages():
    from openminion.modules.llm.schemas import Message

    return [
        Message(role="system", content="You are an operator."),
        Message(role="user", content="read the report and summarize it"),
    ]


def _act_tools():
    from openminion.modules.llm.schemas import ToolSpec

    return [
        ToolSpec(
            name="file.read",
            description="Read a file's bytes.",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        ),
        ToolSpec(
            name="file.write",
            description="Write a file.",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        ),
    ]


def test_non_entry_call_records_serialized_request_attribution(tmp_path):
    from openminion.modules.brain.diagnostics.usage import (
        request_attribution,
    )

    session = LocalSessionStore(tmp_path)
    response = LLMResponse(
        ok=True,
        provider="test",
        model="model",
        output_text="done",
        usage=UsageInfo(input_tokens=11, output_tokens=4),
    )
    messages, tools = _act_messages(), _act_tools()

    with llm_usage_scope(session, "s", "trace", "run"):
        record_llm_response(
            purpose="act", response=response, messages=messages, tools=tools
        )

    payload = session.list_events("s")[0]["payload"]
    expected = request_attribution(messages=messages, tools=tools, response=response)
    assert payload["attribution"] == "serialized_request"
    assert payload["context_bytes"] == expected["context_bytes"] > 0
    assert payload["tool_schema_count"] == 2
    assert payload["tool_schema_bytes"] == expected["tool_schema_bytes"] > 0
    assert (
        payload["request_bytes"] == expected["request_bytes"] > payload["context_bytes"]
    )
    assert payload["context_tokens"] > 0
    assert payload["context_tokens_source"] == "estimate:count_tokens"
    # Provider-billed totals stay separate from the assembled estimate.
    assert payload["usage"]["prompt_tokens"] == 11
    assert payload["usage"]["completion_tokens"] == 4


def test_usage_only_call_omits_unmeasured_context_columns(tmp_path):
    session = LocalSessionStore(tmp_path)
    response = LLMResponse(ok=True, provider="test", model="model")

    with llm_usage_scope(session, "s", "trace", "run"):
        record_llm_response(purpose="reflect", response=response)

    payload = session.list_events("s")[0]["payload"]
    assert payload["attribution"] == "usage_only"
    for column in (
        "context_bytes",
        "context_tokens",
        "tool_schema_bytes",
        "tool_schema_count",
        "request_bytes",
        "response_bytes",
    ):
        assert column not in payload


def test_missing_tools_leaves_the_tool_columns_unmeasured(tmp_path):
    session = LocalSessionStore(tmp_path)
    response = LLMResponse(ok=True, provider="test", model="model")

    with llm_usage_scope(session, "s", "trace", "run"):
        record_llm_response(
            purpose="judge", response=response, messages=_act_messages()
        )

    payload = session.list_events("s")[0]["payload"]
    assert payload["attribution"] == "serialized_request"
    assert payload["context_bytes"] > 0
    # The request wraps the same messages under its own key, so it is slightly larger.
    assert payload["request_bytes"] > payload["context_bytes"]
    for column in ("tool_schema_bytes", "tool_schema_count"):
        assert column not in payload
