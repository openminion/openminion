from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openminion.modules.brain.adapters.session.runtime import SessctlAdapter
from openminion.modules.brain.adapters.session.local_store import LocalSessionStore
from openminion.modules.brain.diagnostics.events import CanonicalEventLogger
from openminion.modules.brain.loop.tools.transcript import (
    persist_blocked_tool_calls,
    persist_requested_tool_calls,
    persist_terminal_tool_result,
    replay_tool_messages,
)
from openminion.modules.brain.schemas import ActionError, ActionResult, ArtifactRef
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
        results = persist_blocked_tool_calls(
            loop_ctx,
            loop_state=loop_state,
            turn_scope_id="turn-2",
            tool_calls=calls,
            code="MIXED_DECOMPOSE_TOOL_CALLS",
            message="mixed control and executable calls",
        )

        assert [result.status for result in results] == ["blocked", "blocked"]

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


def test_security_tool_transcript_persists_only_structural_facts(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionStore(tmp_path / "security-transcript.db")
    try:
        session_id = store.create_session(
            initial_agent_id="security-researcher-readonly",
            profile_version="pv1",
        )
        loop_ctx = SimpleNamespace(
            session_api=SessctlAdapter(store),
            state=SimpleNamespace(session_id=session_id),
        )
        loop_state = SimpleNamespace(scratchpad={})
        report_ref = "artifact://sha256/" + ("a" * 64)
        call = ToolCall(
            id="call-report",
            name="security.publish_report",
            arguments={
                "scope": {
                    "target": "/private/source",
                    "objective": "Bearer request-secret-12345",
                },
                "findings": [{"description": "private finding prose"}],
            },
        )

        persist_requested_tool_calls(
            loop_ctx,
            loop_state=loop_state,
            turn_scope_id="turn-security",
            tool_calls=[call],
        )
        persist_terminal_tool_result(
            loop_ctx,
            loop_state=loop_state,
            turn_scope_id="turn-security",
            tool_call=call,
            action_result=ActionResult(
                command_id="call-report",
                status="success",
                summary="private report summary",
                artifact_refs=[ArtifactRef(ref=report_ref)],
                outputs={
                    "assessment_id": "b" * 32,
                    "execution_status": "partial",
                    "finding_count": 1,
                    "artifact_refs": [report_ref, "/private/report.json"],
                    "findings": [{"description": "private finding prose"}],
                },
            ),
        )

        events = store.get_tool_transcript(session_id)["events"]
        requested = events[0]["payload"]["sanitized_normalized_arguments"]
        output = events[1]["payload"]["output"]
        assert requested == {}
        assert output == {
            "summary": "security tool completed",
            "outputs": {
                "assessment_id": "b" * 32,
                "result_status": "partial",
                "finding_count": 1,
                "artifact_refs": [report_ref],
                "artifact_count": 1,
            },
        }
        assert events[1]["refs"] == {"artifact_refs": [report_ref]}
        assert "/private" not in str(events)
        assert "request-secret" not in str(events)
        assert "private finding prose" not in str(events)

        blocked_call = ToolCall(
            id="call-scan",
            name="security.scan_code",
            arguments={"target": "/private/source"},
        )
        scan_ref = "artifact://sha256/" + ("c" * 64)
        persist_requested_tool_calls(
            loop_ctx,
            loop_state=loop_state,
            turn_scope_id="turn-security-2",
            tool_calls=[blocked_call],
        )
        persist_terminal_tool_result(
            loop_ctx,
            loop_state=loop_state,
            turn_scope_id="turn-security-2",
            tool_call=blocked_call,
            action_result=ActionResult(
                command_id="call-scan",
                status="failed",
                summary="failed at /private/source",
                artifact_refs=[ArtifactRef(ref=scan_ref)],
                error=ActionError(
                    code="EXEC_ERROR",
                    message="Bearer failure-secret-12345",
                    details={"path": "/private/source"},
                ),
            ),
        )
        blocked = store.get_tool_transcript(session_id)["events"][-1]["payload"]
        assert blocked["error"] == {
            "code": "EXEC_ERROR",
            "message": "security tool did not complete",
            "details": {},
        }
        assert store.get_tool_transcript(session_id)["events"][-1]["refs"] == {
            "artifact_refs": [scan_ref]
        }
        assert "failure-secret" not in str(blocked)
    finally:
        store.close()


def test_security_profile_persists_file_reads_as_structural_facts(
    tmp_path: Path,
) -> None:
    session_api = LocalSessionStore(tmp_path / "security-profile")
    loop_ctx = SimpleNamespace(
        session_api=session_api,
        state=SimpleNamespace(
            session_id="security-session",
            agent_id="security-researcher-readonly",
        ),
    )
    loop_state = SimpleNamespace(scratchpad={})
    call = ToolCall(
        id="call-read",
        name="file.read",
        arguments={"path": "/private/source.py"},
    )

    persist_requested_tool_calls(
        loop_ctx,
        loop_state=loop_state,
        turn_scope_id="turn-security-read",
        tool_calls=[call],
    )
    persist_terminal_tool_result(
        loop_ctx,
        loop_state=loop_state,
        turn_scope_id="turn-security-read",
        tool_call=call,
        action_result=ActionResult(
            command_id="call-read",
            status="success",
            summary="read private source",
            outputs={"content": "return eval(user_input)"},
        ),
    )

    events = session_api.get_tool_transcript("security-session")["events"]
    assert events[0]["payload"]["sanitized_normalized_arguments"] == {}
    assert events[1]["payload"]["output"] == {
        "summary": "security tool completed",
        "outputs": {},
    }
    assert "/private" not in str(events)
    assert "eval(user_input)" not in str(events)


def test_security_profile_tool_event_omits_summary(tmp_path: Path) -> None:
    session_api = LocalSessionStore(tmp_path / "security-events")
    logger = CanonicalEventLogger(
        session_api=session_api,
        session_id="security-session",
        agent_id="security-researcher-readonly",
    )

    logger.emit(
        "tool.completed",
        {
            "status": "success",
            "summary": "return eval(user_input)",
            "tool_name": "file.read",
        },
    )

    payload = session_api.list_events("security-session")[0]["payload"]
    assert payload == {"status": "success", "tool_name": "file.read"}
