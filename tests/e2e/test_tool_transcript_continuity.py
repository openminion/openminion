from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.modules.brain.adapters.session.runtime import SessctlAdapter
from openminion.modules.brain.loop.tools.transcript import (
    persist_requested_tool_calls,
    persist_terminal_tool_result,
    replay_tool_messages,
)
from openminion.modules.brain.schemas import ActionError, ActionResult
from openminion.modules.llm.providers.message_payloads import _messages_openai_like
from openminion.modules.llm.schemas import LLMRequest, ToolCall
from openminion.modules.llm.transcript import validate_tool_transcript
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore

pytestmark = pytest.mark.e2e


def _result(call_id: str, *, success: bool) -> ActionResult:
    if success:
        return ActionResult(
            command_id=call_id,
            status="success",
            summary=f"completed {call_id}",
            outputs={"value": call_id},
        )
    return ActionResult(
        command_id=call_id,
        status="failed",
        summary=f"failed {call_id}",
        error=ActionError(code="FIXTURE_FAILURE", message="fixture failure"),
    )


def test_durable_transcript_survives_parallel_failure_serial_recovery_and_resume(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "tool-transcript-e2e.db"
    store = SQLiteSessionStore(db_path)
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )
    session_api = SessctlAdapter(store)
    loop_ctx = SimpleNamespace(
        session_api=session_api,
        state=SimpleNamespace(session_id=session_id),
    )
    loop_state = SimpleNamespace(scratchpad={})
    parallel_calls = [
        ToolCall(id="call-a", name="fixture.read_a", arguments={"path": "a.txt"}),
        ToolCall(id="call-b", name="fixture.read_b", arguments={"path": "b.txt"}),
    ]
    recovery_call = ToolCall(
        id="call-c",
        name="fixture.recover",
        arguments={"source": "call-a"},
        depends_on=["call-a"],
    )

    persist_requested_tool_calls(
        loop_ctx,
        loop_state=loop_state,
        turn_scope_id="turn-e2e",
        tool_calls=parallel_calls,
    )
    persist_terminal_tool_result(
        loop_ctx,
        loop_state=loop_state,
        turn_scope_id="turn-e2e",
        tool_call=parallel_calls[1],
        action_result=_result("call-b", success=False),
    )
    persist_terminal_tool_result(
        loop_ctx,
        loop_state=loop_state,
        turn_scope_id="turn-e2e",
        tool_call=parallel_calls[0],
        action_result=_result("call-a", success=True),
    )
    persist_requested_tool_calls(
        loop_ctx,
        loop_state=loop_state,
        turn_scope_id="turn-e2e",
        tool_calls=[recovery_call],
    )
    persist_terminal_tool_result(
        loop_ctx,
        loop_state=loop_state,
        turn_scope_id="turn-e2e",
        tool_call=recovery_call,
        action_result=_result("call-c", success=True),
    )

    messages = replay_tool_messages(session_api, session_id)
    request = LLMRequest(messages=messages)
    assert validate_tool_transcript(request) == "canonical_events"
    assert [message.role for message in messages] == [
        "assistant",
        "tool",
        "tool",
        "assistant",
        "tool",
    ]
    assert [call.id for call in messages[0].tool_calls] == ["call-a", "call-b"]
    assert [message.tool_call_id for message in messages if message.role == "tool"] == [
        "call-b",
        "call-a",
        "call-c",
    ]
    assert messages[3].tool_calls[0].depends_on == ["call-a"]

    wire = _messages_openai_like(request, include_fallback_instruction=False)
    assert [item["role"] for item in wire] == [
        "assistant",
        "tool",
        "tool",
        "assistant",
        "tool",
    ]
    assert json.loads(wire[0]["tool_calls"][0]["function"]["arguments"]) == {
        "path": "a.txt"
    }
    assert wire[1]["tool_call_id"] == "call-b"

    event_count = len(store.get_tool_transcript(session_id)["events"])
    store.close()
    resumed_api = SessctlAdapter(db_path)
    try:
        resumed = replay_tool_messages(resumed_api, session_id)
        assert resumed == messages
        assert len(resumed_api.get_tool_transcript(session_id)["events"]) == event_count
    finally:
        resumed_api.store.close()


def test_legacy_and_declared_fallback_lanes_remain_explicit(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "legacy-tool-transcript-e2e.db")
    try:
        session_id = store.create_session(
            initial_agent_id="agent.main", profile_version="pv1"
        )
        store.append_event(
            session_id,
            "tool.completed",
            {"tool_name": "legacy.read", "summary": "historical result"},
        )
        session_api = SessctlAdapter(store)

        assert session_api.get_tool_transcript(session_id)["transcript_lane"] == (
            "legacy_history"
        )
        assert replay_tool_messages(session_api, session_id) == []
        assert (
            validate_tool_transcript(
                LLMRequest(
                    messages=[],
                    metadata={"tool_call_strategy": "fallback"},
                )
            )
            == "declared_fallback"
        )
    finally:
        store.close()
