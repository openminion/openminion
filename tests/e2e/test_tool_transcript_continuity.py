from __future__ import annotations

import asyncio
from contextvars import copy_context
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.base.types import Message as BridgeMessage
from openminion.modules.brain.adapters.session.runtime import SessctlAdapter
from openminion.modules.brain.loop.tools import (
    AdaptiveToolLoopProfile,
    run_adaptive_tool_loop,
)
from openminion.modules.brain.loop.tools.transcript import (
    persist_interrupted_tool_calls,
    persist_requested_tool_calls,
    persist_terminal_tool_result,
    replay_tool_messages,
)
from openminion.modules.brain.schemas import (
    ActionError,
    ActionResult,
    BudgetCounters,
    WorkingState,
)
from openminion.modules.brain.tools.executor import CommandExecutionOutcome
from openminion.modules.llm.providers.message_payloads import _messages_openai_like
from openminion.modules.llm.schemas import (
    LLMRequest,
    LLMResponse,
    Message,
    ToolCall,
    ToolSpec,
)
from openminion.modules.llm.transcript import validate_tool_transcript
from openminion.modules.runtime.sync import _turn_cancellations, _turn_deadline
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.storage.backends.postgres import RecordStorePostgres
from openminion.services.brain.post_execution.mixin import BrainBridgeTurnMixin
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.exc import OperationalError as SQLAlchemyOperationalError

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


def test_interrupted_turn_closes_pending_calls_idempotently(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "interrupted-tool-transcript-e2e.db")
    try:
        session_id = store.create_session(
            initial_agent_id="agent.main", profile_version="pv1"
        )
        session_api = SessctlAdapter(store)
        loop_ctx = SimpleNamespace(
            session_api=session_api,
            state=SimpleNamespace(session_id=session_id),
        )
        loop_state = SimpleNamespace(scratchpad={})
        batch = [
            ToolCall(id="call-done", name="fixture.fast", arguments={"k": "1"}),
            ToolCall(id="call-stuck", name="fixture.slow", arguments={"k": "2"}),
        ]
        persist_requested_tool_calls(
            loop_ctx,
            loop_state=loop_state,
            turn_scope_id="turn-1",
            tool_calls=batch,
        )
        persist_terminal_tool_result(
            loop_ctx,
            loop_state=loop_state,
            turn_scope_id="turn-1",
            tool_call=batch[0],
            action_result=_result("call-done", success=True),
        )

        assert (
            persist_interrupted_tool_calls(
                session_api,
                session_id,
                turn_scope_id="other-turn",
                timed_out=True,
            )
            == 0
        )
        assert (
            persist_interrupted_tool_calls(
                session_api,
                session_id,
                turn_scope_id="turn-1",
                timed_out=True,
            )
            == 1
        )
        assert (
            persist_interrupted_tool_calls(
                session_api,
                session_id,
                turn_scope_id="turn-1",
                timed_out=True,
            )
            == 0
        )

        messages = replay_tool_messages(session_api, session_id)
        assert validate_tool_transcript(LLMRequest(messages=messages)) == (
            "canonical_events"
        )
        by_id = {m.tool_call_id: m for m in messages if m.role == "tool"}
        assert by_id["call-done"].tool_status == "success"
        assert by_id["call-stuck"].tool_status == "timeout"
        assert by_id["call-stuck"].tool_error == {
            "code": "TURN_TIMEOUT",
            "details": {},
            "message": "Turn timed out before tool call completed.",
        }
    finally:
        store.close()


class _FaultySessionProxy:
    def __init__(
        self,
        delegate: SessctlAdapter,
        *,
        read_error: BaseException | None = None,
        blocked_write_error: BaseException | None = None,
        fail_blocked_write_at: int = 1,
    ) -> None:
        self._delegate = delegate
        self.read_error = read_error
        self.blocked_write_error = blocked_write_error
        self.fail_blocked_write_at = fail_blocked_write_at
        self.blocked_write_attempts = 0

    def get_tool_transcript(self, session_id: str) -> dict[str, Any]:
        if self.read_error is not None:
            raise self.read_error
        return self._delegate.get_tool_transcript(session_id)

    def append_event(
        self,
        session_id: str,
        type: str | None = None,
        payload: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        if type == "tool.call.blocked" and self.blocked_write_error is not None:
            self.blocked_write_attempts += 1
            if self.blocked_write_attempts >= self.fail_blocked_write_at:
                raise self.blocked_write_error
        return self._delegate.append_event(
            session_id,
            type=str(type or ""),
            payload=dict(payload or {}),
            **kwargs,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class _SingleBatchRuntime:
    def __init__(self, tool_calls: list[ToolCall]) -> None:
        self._tool_calls = tool_calls

    def complete(self, **kwargs: Any) -> LLMResponse:
        del kwargs
        return LLMResponse(
            ok=True,
            provider="fake",
            model="fake-model",
            output_text="",
            assistant_messages=[
                Message(role="assistant", content="", tool_calls=self._tool_calls)
            ],
            tool_calls=self._tool_calls,
            finish_reason="tool_calls",
        )


def _adaptive_profile(*names: str) -> AdaptiveToolLoopProfile:
    return AdaptiveToolLoopProfile(
        profile_name="transcript_recovery_test",
        mode_name="act_adaptive",
        allowed_tools=frozenset(names),
        max_iterations=2,
        max_tool_calls_per_loop=4,
        allow_llm_recovery_after_tool_failure=True,
        tool_choice="auto",
        provider_parallel_tool_capacity=2,
    )


def _tool_specs(*names: str) -> list[ToolSpec]:
    return [
        ToolSpec(
            name=name,
            description=name,
            input_schema={"type": "object", "properties": {}},
        )
        for name in names
    ]


def _loop_state_obj(
    session_id: str,
    *,
    trace_id: str,
    research_invocation_id: str,
) -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="agent.main",
        trace_id=trace_id,
        module_state={
            "research": {
                "child": True,
                "invocation_id": research_invocation_id,
            }
        },
        budgets_remaining=BudgetCounters(
            ticks=6,
            tool_calls=6,
            a2a_calls=0,
            tokens=4000,
            time_ms=60000,
        ),
        llm_calls_max=4,
    )


def test_run_adaptive_tool_loop_preserves_failure_and_snapshot_on_recovery_errors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = SQLiteSessionStore(tmp_path / "adaptive-loop-recovery.db")
    try:
        session_id = store.create_session(
            initial_agent_id="agent.main", profile_version="pv1"
        )
        base_api = SessctlAdapter(store)

        read_fail_proxy = _FaultySessionProxy(
            base_api, read_error=OSError("PRIVATE_ADAPTIVE_READ_SECRET_1")
        )
        state_read = _loop_state_obj(
            session_id, trace_id="turn-read-err", research_invocation_id="inv-read"
        )
        primary_read_timeout = TimeoutError("loop timed out on read path")
        ctx_read = SimpleNamespace(
            state=state_read,
            session_api=read_fail_proxy,
            structural_tool_results=False,
            provider_retry_max_attempts=1,
            emit_status=lambda **_: None,
            apply_turn_steering=lambda _: [],
            execute_command=lambda **_: None,
        )
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            with pytest.raises(TimeoutError) as read_exc_info:
                run_adaptive_tool_loop(
                    ctx_read,
                    profile=_adaptive_profile("fixture.slow"),
                    runtime=_SingleBatchRuntime(
                        [ToolCall(id="c-read", name="fixture.slow", arguments={})]
                    ),
                    model="fake-model",
                    initial_messages=[Message(role="user", content="go")],
                    tool_specs=_tool_specs("fixture.slow"),
                    tool_batch_runner=lambda **_: (_ for _ in ()).throw(
                        primary_read_timeout
                    ),
                )
        assert read_exc_info.value is primary_read_timeout
        assert "adaptive_loop" in state_read.module_state
        read_notes = "\n".join(getattr(primary_read_timeout, "__notes__", []))
        assert "(error_type=OSError; closed=0)" in read_notes
        assert "PRIVATE_ADAPTIVE_READ_SECRET_1" not in read_notes
        assert "PRIVATE_ADAPTIVE_READ_SECRET_1" not in caplog.text
        assert "brain.tool_transcript.recovery_failed" in caplog.text
        assert all(rec.exc_info is None for rec in caplog.records)

        write_fail_proxy = _FaultySessionProxy(
            base_api, blocked_write_error=OSError("PRIVATE_ADAPTIVE_WRITE_SECRET_2")
        )
        state_write = _loop_state_obj(
            session_id, trace_id="turn-write-err", research_invocation_id="inv-write"
        )
        primary_write_timeout = TimeoutError("loop timed out on write path")
        ctx_write = SimpleNamespace(
            state=state_write,
            session_api=write_fail_proxy,
            structural_tool_results=False,
            provider_retry_max_attempts=1,
            emit_status=lambda **_: None,
            apply_turn_steering=lambda _: [],
            execute_command=lambda **_: None,
        )
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            with pytest.raises(TimeoutError) as write_exc_info:
                run_adaptive_tool_loop(
                    ctx_write,
                    profile=_adaptive_profile("fixture.slow"),
                    runtime=_SingleBatchRuntime(
                        [ToolCall(id="c-write", name="fixture.slow", arguments={})]
                    ),
                    model="fake-model",
                    initial_messages=[Message(role="user", content="go")],
                    tool_specs=_tool_specs("fixture.slow"),
                    tool_batch_runner=lambda **_: (_ for _ in ()).throw(
                        primary_write_timeout
                    ),
                )
        assert write_exc_info.value is primary_write_timeout
        assert "adaptive_loop" in state_write.module_state
        write_notes = "\n".join(getattr(primary_write_timeout, "__notes__", []))
        assert "(error_type=OSError; closed=0)" in write_notes
        assert "PRIVATE_ADAPTIVE_WRITE_SECRET_2" not in write_notes
        assert "PRIVATE_ADAPTIVE_WRITE_SECRET_2" not in caplog.text
        assert "brain.tool_transcript.recovery_failed" in caplog.text
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            with pytest.raises(OSError, match="PRIVATE_ADAPTIVE_WRITE_SECRET_2"):
                persist_interrupted_tool_calls(
                    write_fail_proxy,
                    session_id,
                    turn_scope_id="turn-write-err",
                    timed_out=True,
                )
        assert "PRIVATE_ADAPTIVE_WRITE_SECRET_2" not in caplog.text

        assert (
            persist_interrupted_tool_calls(
                base_api,
                session_id,
                turn_scope_id="turn-read-err",
                timed_out=True,
            )
            == 1
        )
        assert (
            persist_interrupted_tool_calls(
                base_api,
                session_id,
                turn_scope_id="turn-write-err",
                timed_out=True,
            )
            == 1
        )

        state_cancel = _loop_state_obj(
            session_id, trace_id="turn-cancel", research_invocation_id="inv-cancel"
        )
        cancelled_exc = asyncio.CancelledError("research cancelled")

        def _partial_batch_runner(**kwargs: Any) -> list[tuple[Any, Any]]:
            calls = kwargs["tool_calls"]
            done_outcome = CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=_result("c-done", success=True),
            )
            persist_terminal_tool_result(
                ctx_cancel,
                loop_state=SimpleNamespace(
                    scratchpad={
                        "tool_transcript.request_event_ids": {
                            "c-done": next(
                                ev["event_id"]
                                for ev in base_api.get_tool_transcript(session_id)[
                                    "events"
                                ]
                                if ev.get("event_type") == "tool.call.requested"
                                and (ev.get("payload") or {}).get("call_id") == "c-done"
                            )
                        }
                    }
                ),
                turn_scope_id="turn-cancel",
                tool_call=calls[0],
                action_result=done_outcome.action_result,
            )
            raise cancelled_exc

        ctx_cancel = SimpleNamespace(
            state=state_cancel,
            session_api=base_api,
            structural_tool_results=True,
            provider_retry_max_attempts=1,
            emit_status=lambda **_: None,
            apply_turn_steering=lambda _: [],
            execute_command=lambda **_: None,
        )
        with pytest.raises(asyncio.CancelledError) as cancel_info:
            run_adaptive_tool_loop(
                ctx_cancel,
                profile=_adaptive_profile("fixture.fast", "fixture.secret"),
                runtime=_SingleBatchRuntime(
                    [
                        ToolCall(
                            id="c-done",
                            name="fixture.fast",
                            arguments={"secret": "omit-me"},
                        ),
                        ToolCall(
                            id="c-sec",
                            name="fixture.secret",
                            arguments={"secret": "omit-me-too"},
                        ),
                    ]
                ),
                model="fake-model",
                initial_messages=[Message(role="user", content="go")],
                tool_specs=_tool_specs("fixture.fast", "fixture.secret"),
                tool_batch_runner=_partial_batch_runner,
            )
        assert cancel_info.value is cancelled_exc
        assert "adaptive_loop" in state_cancel.module_state
        events = [
            ev
            for ev in base_api.get_tool_transcript(session_id)["events"]
            if (ev.get("payload") or {}).get("turn_scope_id") == "turn-cancel"
        ]
        by_call_terminal = {
            ev["payload"]["call_id"]: ev
            for ev in events
            if ev["event_type"] in {"tool.call.completed", "tool.call.blocked"}
        }
        assert by_call_terminal["c-done"]["event_type"] == "tool.call.completed"
        assert by_call_terminal["c-done"]["payload"]["research_invocation_id"] == (
            "inv-cancel"
        )
        assert by_call_terminal["c-sec"]["event_type"] == "tool.call.blocked"
        assert by_call_terminal["c-sec"]["payload"] == {
            "schema_version": 1,
            "turn_scope_id": "turn-cancel",
            "call_id": "c-sec",
            "research_invocation_id": "inv-cancel",
            "status": "blocked",
            "error": {
                "code": "TURN_INTERRUPTED",
                "message": "security tool did not complete",
                "details": {},
            },
        }
        assert (
            validate_tool_transcript(
                LLMRequest(messages=replay_tool_messages(base_api, session_id))
            )
            == "canonical_events"
        )
    finally:
        store.close()


class _BridgeHarness(BrainBridgeTurnMixin):
    def __init__(self, runner: Any) -> None:
        self._runner = runner
        self._telemetryctl = object()
        self._identity_agent_id = "agent.bridge"
        self._logger = logging.getLogger("openminion.test.bridge")
        self.emitted: list[tuple[str, str]] = []
        self.turn_error: BaseException = TimeoutError("bridge timed out")

    def _resolve_turn_session_ids(self, *, message: BridgeMessage) -> tuple[str, str]:
        sid = str(message.metadata.get("session_id") or "bridge-session")
        return sid, sid

    async def _prepare_turn(self, **kwargs: Any) -> tuple[Any, str, str, str, float]:
        sid = kwargs["brain_session_id"]
        req_id = str(kwargs["message"].metadata.get("request_id") or "req-1")
        return self._runner, sid, req_id, f"{sid}_1", 1.0

    def _bind_execution_telemetry(self, **kwargs: Any) -> None:
        del kwargs

    def _unbind_execution_telemetry(self, **kwargs: Any) -> None:
        del kwargs

    async def _emit_agent_event(self, **kwargs: Any) -> bool:
        self.emitted.append((str(kwargs["event_type"]), str(kwargs["status"])))
        return True

    async def emit_invocation_lifecycle(self, fact: Any) -> None:
        self.emitted.append((str(fact.event_type), "lifecycle"))

    def _execute_turn(self, **kwargs: Any) -> Any:
        del kwargs
        raise self.turn_error


def test_brain_bridge_run_turn_records_failure_telemetry_before_recovery_errors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = SQLiteSessionStore(tmp_path / "bridge-recovery.db")
    try:
        session_id = store.create_session(
            initial_agent_id="agent.bridge", profile_version="pv1"
        )
        base_api = SessctlAdapter(store)
        persist_requested_tool_calls(
            SimpleNamespace(
                session_api=base_api, state=SimpleNamespace(session_id=session_id)
            ),
            loop_state=SimpleNamespace(scratchpad={}),
            turn_scope_id="req-bridge-write",
            tool_calls=[
                ToolCall(id="call-bw-1", name="fixture.fast", arguments={}),
                ToolCall(id="call-bw-2", name="fixture.slow", arguments={}),
            ],
        )

        read_proxy = _FaultySessionProxy(
            base_api, read_error=OSError("PRIVATE_BRIDGE_READ_SECRET_3")
        )
        bridge_read = _BridgeHarness(
            SimpleNamespace(
                session_api=read_proxy,
                profile=SimpleNamespace(structural_tool_results=True),
                tool_api=None,
            )
        )
        timeout_exc = TimeoutError("bridge read timeout")
        bridge_read.turn_error = timeout_exc
        msg_read = BridgeMessage(
            id="m-1",
            channel="api",
            target="u",
            body="hi",
            metadata={"session_id": session_id, "request_id": "req-bridge-read"},
        )
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            with pytest.raises(TimeoutError) as read_err:
                asyncio.run(bridge_read.run_turn(msg_read))
        assert str(read_err.value) == "bridge read timeout"
        assert ("agent.turn.failed", "failed") in bridge_read.emitted
        assert ("agent.execution.failed", "failed") in bridge_read.emitted
        bridge_read_notes = "\n".join(getattr(read_err.value, "__notes__", []))
        assert "(error_type=OSError; closed=0)" in bridge_read_notes
        assert "PRIVATE_BRIDGE_READ_SECRET_3" not in bridge_read_notes
        assert "PRIVATE_BRIDGE_READ_SECRET_3" not in caplog.text
        assert "brain.tool_transcript.recovery_failed" in caplog.text
        assert all(rec.exc_info is None for rec in caplog.records)

        write_proxy = _FaultySessionProxy(
            base_api,
            blocked_write_error=OSError("PRIVATE_BRIDGE_WRITE_SECRET_4"),
            fail_blocked_write_at=2,
        )
        bridge_write = _BridgeHarness(
            SimpleNamespace(
                session_api=write_proxy,
                profile=SimpleNamespace(structural_tool_results=True),
                tool_api=None,
            )
        )
        cancel_exc = asyncio.CancelledError("bridge cancelled")
        bridge_write.turn_error = cancel_exc
        msg_write = BridgeMessage(
            id="m-2",
            channel="api",
            target="u",
            body="hi",
            metadata={"session_id": session_id, "request_id": "req-bridge-write"},
        )
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            with pytest.raises(asyncio.CancelledError) as write_err:
                asyncio.run(bridge_write.run_turn(msg_write))
        assert str(write_err.value) == "bridge cancelled"
        assert ("agent.turn.failed", "failed") in bridge_write.emitted
        assert ("agent.execution.cancelled", "cancelled") in bridge_write.emitted
        bridge_write_notes = "\n".join(getattr(write_err.value, "__notes__", []))
        assert "(error_type=OSError; closed=1)" in bridge_write_notes
        assert "PRIVATE_BRIDGE_WRITE_SECRET_4" not in bridge_write_notes
        assert "PRIVATE_BRIDGE_WRITE_SECRET_4" not in caplog.text
        assert "closed=1" in caplog.text
        assert all(rec.exc_info is None for rec in caplog.records)
    finally:
        store.close()


def test_postgres_sqlalchemy_recovery_boundary_and_programming_error_passthrough(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db_path = tmp_path / "pg-record-store-session.db"
    pg_store = SQLiteSessionStore(db_path, artifactctl=None)
    sqlite_record_store = pg_store._record_store
    sa_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    pg_record_store = RecordStorePostgres(sa_engine)
    pg_store._record_store = pg_record_store
    pg_store._initialize_store_components()
    fault_state = {"mode": "none", "blocked_inserts": 0}

    def _inject_sa_fault(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        normalized_sql = " ".join(statement.split())
        if (
            fault_state["mode"] == "select"
            and "SELECT * FROM session_events" in normalized_sql
        ):
            raise SQLAlchemyOperationalError(
                "SELECT * FROM session_events WHERE secret='PRIVATE_PG_SQL_SECRET'",
                params={"sid": "PRIVATE_PG_PARAM_SECRET"},
                orig=RuntimeError("PRIVATE_PG_DRIVER_SECRET"),
            )
        if fault_state["mode"] == "second_insert" and normalized_sql.startswith(
            "INSERT INTO session_events("
        ):
            fault_state["blocked_inserts"] += 1
            if fault_state["blocked_inserts"] >= 2:
                raise SQLAlchemyOperationalError(
                    "INSERT INTO session_events VALUES ('PRIVATE_PG_WRITE_SQL')",
                    params={"p": "PRIVATE_PG_WRITE_PARAM"},
                    orig=RuntimeError("PRIVATE_PG_WRITE_DRIVER"),
                )

    sa_event.listen(sa_engine, "before_cursor_execute", _inject_sa_fault)
    try:
        session_id = pg_store.create_session(
            initial_agent_id="agent.pg", profile_version="pv1"
        )
        pg_api = SessctlAdapter(pg_store)
        loop_ctx = SimpleNamespace(
            session_api=pg_api,
            state=SimpleNamespace(session_id=session_id),
            structural_tool_results=True,
        )
        persist_requested_tool_calls(
            loop_ctx,
            loop_state=SimpleNamespace(scratchpad={}),
            turn_scope_id="req-pg-timeout",
            tool_calls=[ToolCall(id="call-pg-t", name="fixture.slow", arguments={})],
        )
        persist_requested_tool_calls(
            loop_ctx,
            loop_state=SimpleNamespace(scratchpad={}),
            turn_scope_id="req-pg-cancel",
            tool_calls=[
                ToolCall(id="call-pg-c1", name="fixture.fast", arguments={}),
                ToolCall(id="call-pg-c2", name="fixture.slow", arguments={}),
            ],
        )
        assert (
            persist_interrupted_tool_calls(
                pg_api, session_id, turn_scope_id="", timed_out=True
            )
            == 0
        )

        fault_state["mode"] = "select"
        bridge_pg = _BridgeHarness(
            SimpleNamespace(
                session_api=pg_api,
                profile=SimpleNamespace(structural_tool_results=True),
                tool_api=None,
            )
        )
        pg_timeout = TimeoutError("pg bridge timeout")
        bridge_pg.turn_error = pg_timeout
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            with pytest.raises(TimeoutError) as pg_timeout_err:
                asyncio.run(
                    bridge_pg.run_turn(
                        BridgeMessage(
                            id="m-pg-1",
                            channel="api",
                            target="u",
                            body="hi",
                            metadata={
                                "session_id": session_id,
                                "request_id": "req-pg-timeout",
                            },
                        )
                    )
                )
        assert str(pg_timeout_err.value) == "pg bridge timeout"
        assert ("agent.turn.failed", "failed") in bridge_pg.emitted
        assert ("agent.execution.failed", "failed") in bridge_pg.emitted
        pg_read_notes = "\n".join(getattr(pg_timeout_err.value, "__notes__", []))
        assert "(error_type=OperationalError; closed=0)" in pg_read_notes
        for secret in (
            "PRIVATE_PG_SQL_SECRET",
            "PRIVATE_PG_PARAM_SECRET",
            "PRIVATE_PG_DRIVER_SECRET",
        ):
            assert secret not in pg_read_notes
            assert secret not in caplog.text

        caplog.clear()
        with caplog.at_level(logging.WARNING):
            with pytest.raises(SQLAlchemyOperationalError):
                persist_interrupted_tool_calls(
                    pg_api,
                    session_id,
                    turn_scope_id="req-pg-timeout",
                    structural_only=True,
                    timed_out=True,
                )
        for secret in (
            "PRIVATE_PG_SQL_SECRET",
            "PRIVATE_PG_PARAM_SECRET",
            "PRIVATE_PG_DRIVER_SECRET",
        ):
            assert secret not in caplog.text

        fault_state["mode"] = "second_insert"
        fault_state["blocked_inserts"] = 0
        pg_cancel = asyncio.CancelledError("pg bridge cancelled")
        bridge_pg.turn_error = pg_cancel
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            with pytest.raises(asyncio.CancelledError) as pg_cancel_err:
                asyncio.run(
                    bridge_pg.run_turn(
                        BridgeMessage(
                            id="m-pg-2",
                            channel="api",
                            target="u",
                            body="hi",
                            metadata={
                                "session_id": session_id,
                                "request_id": "req-pg-cancel",
                            },
                        )
                    )
                )
        assert str(pg_cancel_err.value) == "pg bridge cancelled"
        pg_write_notes = "\n".join(getattr(pg_cancel_err.value, "__notes__", []))
        assert "(error_type=OperationalError; closed=1)" in pg_write_notes
        for secret in (
            "PRIVATE_PG_WRITE_SQL",
            "PRIVATE_PG_WRITE_PARAM",
            "PRIVATE_PG_WRITE_DRIVER",
        ):
            assert secret not in pg_write_notes
            assert secret not in caplog.text

        fault_state["mode"] = "none"
        for prog_err in (
            TypeError("unexpected type bug"),
            KeyError("unexpected key bug"),
            NotImplementedError("unexpected unimplemented bug"),
        ):
            prog_proxy = _FaultySessionProxy(pg_api, read_error=prog_err)
            with pytest.raises(type(prog_err)):
                persist_interrupted_tool_calls(
                    prog_proxy,
                    session_id,
                    turn_scope_id="req-pg-timeout",
                    active_exception=TimeoutError("outer timeout"),
                )

        assert (
            persist_interrupted_tool_calls(
                pg_api,
                session_id,
                turn_scope_id="req-pg-timeout",
                structural_only=True,
                timed_out=True,
            )
            == 1
        )
        assert (
            persist_interrupted_tool_calls(
                pg_api,
                session_id,
                turn_scope_id="req-pg-cancel",
                structural_only=True,
                timed_out=False,
            )
            == 1
        )
        assert (
            validate_tool_transcript(
                LLMRequest(messages=replay_tool_messages(pg_api, session_id))
            )
            == "canonical_events"
        )
    finally:
        pg_store.close()
        sqlite_record_store.close()
        sa_engine.dispose()


def test_idempotency_conflict_requires_matching_terminal_readback_and_late_worker(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionStore(tmp_path / "idempotency-readback.db")
    try:
        session_id = store.create_session(
            initial_agent_id="agent.main", profile_version="pv1"
        )
        base_api = SessctlAdapter(store)
        loop_ctx = SimpleNamespace(
            session_api=base_api,
            state=SimpleNamespace(
                session_id=session_id,
                module_state={"research": {"child": True, "invocation_id": "inv-race"}},
            ),
        )
        loop_state = SimpleNamespace(scratchpad={})
        race_call = ToolCall(id="call-race", name="fixture.fast", arguments={})
        unverified_call = ToolCall(
            id="call-unverified", name="fixture.slow", arguments={}
        )
        persist_requested_tool_calls(
            loop_ctx,
            loop_state=loop_state,
            turn_scope_id="turn-race",
            tool_calls=[race_call],
        )
        persist_requested_tool_calls(
            loop_ctx,
            loop_state=loop_state,
            turn_scope_id="turn-unverified",
            tool_calls=[unverified_call],
        )

        class _ConcurrentWinnerAdapter:
            def get_tool_transcript(self, sid: str) -> dict[str, Any]:
                return base_api.get_tool_transcript(sid)

            def append_event(
                self,
                sid: str,
                type: str | None = None,
                payload: dict[str, Any] | None = None,
                **kwargs: Any,
            ) -> str:
                if type == "tool.call.blocked":
                    persist_terminal_tool_result(
                        loop_ctx,
                        loop_state=loop_state,
                        turn_scope_id="turn-race",
                        tool_call=race_call,
                        action_result=_result("call-race", success=True),
                    )
                return base_api.append_event(
                    sid, type=str(type or ""), payload=dict(payload or {}), **kwargs
                )

        assert (
            persist_interrupted_tool_calls(
                _ConcurrentWinnerAdapter(),
                session_id,
                turn_scope_id="turn-race",
                timed_out=True,
            )
            == 0
        )
        replayed = {
            m.tool_call_id: m
            for m in replay_tool_messages(base_api, session_id)
            if m.role == "tool"
        }
        assert replayed["call-race"].tool_status == "success"

        class _UnbackedConflictAdapter:
            def get_tool_transcript(self, sid: str) -> dict[str, Any]:
                return base_api.get_tool_transcript(sid)

            def append_event(self, sid: str, **kwargs: Any) -> str:
                del sid, kwargs
                raise ValueError("session event idempotency conflict: unbacked-event")

        with pytest.raises(
            ValueError, match="session event idempotency conflict: unbacked-event"
        ):
            persist_interrupted_tool_calls(
                _UnbackedConflictAdapter(),
                session_id,
                turn_scope_id="turn-unverified",
                timed_out=True,
            )

        assert (
            persist_interrupted_tool_calls(
                base_api,
                session_id,
                turn_scope_id="turn-unverified",
                timed_out=True,
            )
            == 1
        )

        cancel_flag = SimpleNamespace(is_set=lambda: True)

        def _late_worker_call() -> None:
            token_c = _turn_cancellations.set((cancel_flag,))
            token_d = _turn_deadline.set(0.0)
            try:
                persist_terminal_tool_result(
                    loop_ctx,
                    loop_state=loop_state,
                    turn_scope_id="turn-unverified",
                    tool_call=unverified_call,
                    action_result=_result("call-unverified", success=True),
                )
            finally:
                _turn_deadline.reset(token_d)
                _turn_cancellations.reset(token_c)

        with pytest.raises(asyncio.CancelledError):
            copy_context().run(_late_worker_call)
    finally:
        store.close()
