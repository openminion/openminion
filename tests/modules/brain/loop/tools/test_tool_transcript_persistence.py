from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openminion.modules.brain.adapters.session.runtime import SessctlAdapter
from openminion.modules.brain.adapters.session.local_store import LocalSessionStore
from openminion.modules.brain.loop.tools.transcript import (
    persist_blocked_tool_calls,
    persist_requested_tool_calls,
    persist_terminal_tool_result,
    replay_tool_messages,
)
from openminion.modules.brain.schemas import ActionError, ActionResult
from openminion.modules.llm.schemas import ToolCall
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


def test_runtime_emitter_persists_batch_order_and_completion_order(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionStore(tmp_path / "runtime-transcript.db")
    try:
        session_id = store.create_session(
            initial_agent_id="agent.main", profile_version="pv1"
        )
        loop_ctx = SimpleNamespace(
            session_api=SessctlAdapter(store),
            state=SimpleNamespace(session_id=session_id),
        )
        loop_state = SimpleNamespace(scratchpad={})
        calls = [
            ToolCall(
                id="call-list",
                name="file.list_dir",
                arguments={"path": "/tmp"},
            ),
            ToolCall(
                id="call-read",
                name="file.read",
                arguments={"path": "/tmp/a.txt"},
                depends_on=["call-list"],
            ),
        ]

        persist_requested_tool_calls(
            loop_ctx,
            loop_state=loop_state,
            turn_scope_id="turn-1",
            tool_calls=calls,
        )
        persist_terminal_tool_result(
            loop_ctx,
            loop_state=loop_state,
            turn_scope_id="turn-1",
            tool_call=calls[1],
            action_result=ActionResult(
                command_id="call-read",
                status="success",
                summary="read",
                outputs={"content": "hello"},
            ),
        )
        persist_terminal_tool_result(
            loop_ctx,
            loop_state=loop_state,
            turn_scope_id="turn-1",
            tool_call=calls[0],
            action_result=ActionResult(
                command_id="call-list",
                status="failed",
                summary="list failed",
                error=ActionError(code="IO_ERROR", message="unavailable"),
            ),
        )

        transcript = store.get_tool_transcript(session_id)
        events = transcript["events"]

        assert transcript["transcript_lane"] == "canonical_events"
        assert [event["event_type"] for event in events] == [
            "tool.call.requested",
            "tool.call.requested",
            "tool.call.completed",
            "tool.call.blocked",
        ]
        assert [
            events[0]["payload"]["batch_index"],
            events[1]["payload"]["batch_index"],
        ] == [
            0,
            1,
        ]
        assert events[2]["payload"]["call_id"] == "call-read"
        assert events[3]["payload"]["status"] == "error"
        assert events[2]["parent_event_id"] == events[1]["event_id"]
        assert events[3]["parent_event_id"] == events[0]["event_id"]

        replayed = replay_tool_messages(loop_ctx.session_api, session_id)
        assert [message.role for message in replayed] == ["assistant", "tool", "tool"]
        assert [call.id for call in replayed[0].tool_calls] == [
            "call-list",
            "call-read",
        ]
        assert replayed[0].tool_calls[1].arguments == {"path": "/tmp/a.txt"}
        assert [message.tool_call_id for message in replayed[1:]] == [
            "call-read",
            "call-list",
        ]
        assert all("tool_arguments" not in message.meta for message in replayed[1:])
    finally:
        store.close()


def test_runtime_emitter_closes_rejected_batch_as_blocked(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "blocked-transcript.db")
    try:
        session_id = store.create_session(
            initial_agent_id="agent.main", profile_version="pv1"
        )
        loop_ctx = SimpleNamespace(
            session_api=store,
            state=SimpleNamespace(session_id=session_id),
        )
        loop_state = SimpleNamespace(scratchpad={})
        calls = [
            ToolCall(id="call-control", name="decompose", arguments={}),
            ToolCall(id="call-file", name="file.read", arguments={"path": "/tmp/a"}),
        ]

        persist_requested_tool_calls(
            loop_ctx,
            loop_state=loop_state,
            turn_scope_id="turn-2",
            tool_calls=calls,
        )
        persist_blocked_tool_calls(
            loop_ctx,
            loop_state=loop_state,
            turn_scope_id="turn-2",
            tool_calls=calls,
            code="MIXED_DECOMPOSE_TOOL_CALLS",
            message="mixed control and executable calls",
        )

        events = store.get_tool_transcript(session_id)["events"]
        assert [event["event_type"] for event in events] == [
            "tool.call.requested",
            "tool.call.requested",
            "tool.call.blocked",
            "tool.call.blocked",
        ]
        assert [event["payload"]["status"] for event in events[2:]] == [
            "blocked",
            "blocked",
        ]
        assert [event["parent_event_id"] for event in events[2:]] == [
            events[0]["event_id"],
            events[1]["event_id"],
        ]
    finally:
        store.close()


def test_local_session_adapter_preserves_parent_linkage_and_replay(
    tmp_path: Path,
) -> None:
    session_api = LocalSessionStore(tmp_path / "local-sessions")
    loop_ctx = SimpleNamespace(
        session_api=session_api,
        state=SimpleNamespace(session_id="local-session"),
    )
    loop_state = SimpleNamespace(scratchpad={})
    call = ToolCall(id="call-local", name="time", arguments={"timezone": "UTC"})

    persist_requested_tool_calls(
        loop_ctx,
        loop_state=loop_state,
        turn_scope_id="turn-local",
        tool_calls=[call],
    )
    persist_terminal_tool_result(
        loop_ctx,
        loop_state=loop_state,
        turn_scope_id="turn-local",
        tool_call=call,
        action_result=ActionResult(
            command_id="call-local",
            status="success",
            outputs={"utc": "2026-08-13T00:00:00Z"},
        ),
    )

    messages = replay_tool_messages(session_api, "local-session")
    assert [message.role for message in messages] == ["assistant", "tool"]
    assert messages[0].tool_calls[0].arguments == {"timezone": "UTC"}
    assert messages[1].tool_call_id == "call-local"
