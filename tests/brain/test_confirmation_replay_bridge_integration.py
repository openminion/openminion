from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.runner.tick.confirmation import (
    process as confirmation_process,
)
from openminion.modules.brain.runner.tick.context import TickRunContext
from openminion.modules.brain.loop.tools.confirmation import (
    attach_confirmation_replay_queue,
)
from openminion.modules.brain.schemas import ToolCommand, WorkingState
from openminion.services.brain.post_execution import BrainBridgeTurnMixin


class _DummyBridge(BrainBridgeTurnMixin):
    pass


class _DummySessionAPI:
    def __init__(self, state: dict[str, Any]) -> None:
        self._state = dict(state)
        self.written: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []

    def get_latest_working_state(self, session_id: str) -> dict[str, Any]:
        del session_id
        return dict(self._state)

    def put_working_state(
        self, session_id: str, *, state_inline: dict[str, Any]
    ) -> None:
        del session_id
        self.written = dict(state_inline)

    def append_event(
        self,
        session_id: str,
        type: str,
        payload: dict[str, Any],
        *,
        trace_id: str | None = None,
        **_kwargs: Any,
    ) -> str:
        self.events.append(
            {
                "session_id": session_id,
                "type": type,
                "payload": dict(payload),
                "trace_id": trace_id,
            }
        )
        return f"{session_id}-event-{len(self.events)}"


class _DummyPolicyAPI:
    def __init__(self) -> None:
        from openminion.modules.policy.runtime.service import (
            parse_confirmation_response,
        )

        self._delegate = parse_confirmation_response

    def parse_confirmation_response(self, text: str) -> str:
        return str(self._delegate(text)).strip().lower()


class _GrantingPolicyAPI(_DummyPolicyAPI):
    def __init__(self, grant_ids: list[str]) -> None:
        super().__init__()
        self._grant_ids = list(grant_ids)

    def grant_once_from_confirmation(self, **_kwargs: Any) -> str:
        return self._grant_ids.pop(0)


class _DummyRunner:
    def __init__(self, state: dict[str, Any], *, policy_api: Any | None = None) -> None:
        self.session_api = _DummySessionAPI(state)
        self.policy_api = policy_api or _DummyPolicyAPI()
        self.options = SimpleNamespace(
            adaptive_replan_retained_step_outputs=0,
            max_replans=0,
            max_retries_per_step=0,
        )
        self.profile = SimpleNamespace(
            budgets=SimpleNamespace(
                max_ticks_per_user_turn=8,
                max_tool_calls=8,
                max_a2a_calls=0,
                max_total_llm_tokens=100000,
                max_elapsed_ms=45000,
            )
        )


class _CapturingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, name: str, payload: dict[str, Any], *, trace_id: Any = None) -> None:
        del trace_id
        self.events.append((name, dict(payload)))


def _build_inline_state_with_pending_file_write() -> tuple[dict[str, Any], ToolCommand]:
    pending = ToolCommand(
        title="write README",
        tool_name="file.write",
        inputs={"path": "./tmp-bbpc/README.md", "body": "hi"},
        requires_confirmation=True,
        idempotency_key="idem-bbpc-1",
    )
    state_inline = {
        "session_id": "sess-bbpc-int",
        "agent_id": "agent-bbpc-int",
        "budgets_remaining": {
            "ticks": 8,
            "tool_calls": 8,
            "a2a_calls": 0,
            "tokens": 100000,
            "time_ms": 45000,
        },
        "trace_id": "trace-bbpc-int",
        "status": "waiting_user",
        "phase": "ACT",
        "goal": "write a small README",
        "last_user_input": "write a small README",
        "pending_confirmation_command": pending.model_dump(mode="json"),
        "pending_confirmation_sub_intents": [],
        "pending_confirmation_sub_intent_refs": [],
        "pending_confirmation_goal": "write a small README",
        "pending_confirmation_last_user_input": "write a small README",
        "pending_confirmation_rationale": "user asked to write a file",
        "pending_confirmation_success_criteria": {},
        "pending_confirmation_feasibility_state": {},
        "pending_confirmation_feasibility_report": None,
        "plan": None,
        "cursor": 0,
        "constraints": [],
        "decision_feasibility_state": {},
        "awaiting_continuation_reply": False,
        "continuation_guard_command_signature": "",
    }
    return state_inline, pending


def test_bridge_reset_yes_preserves_state_then_runner_replays_seeded_command() -> None:
    inline_state, original_pending = _build_inline_state_with_pending_file_write()

    # --- Step 1: bridge reset ---
    bridge_runner = _DummyRunner(inline_state)
    bridge = _DummyBridge()
    bridge._reset_state_for_new_input(
        runner=bridge_runner,
        session_id="sess-bbpc-int",
        user_input="yes",
    )
    written = bridge_runner.session_api.written
    assert written is not None, "bridge reset must persist updated state"
    assert written["pending_confirmation_command"] is not None, (
        "BBPC fix must preserve pending command on parsed `affirm`"
    )

    # --- Step 2: rebuild WorkingState from bridge output ---
    state = WorkingState.model_validate(written)
    assert state.pending_confirmation_command is not None
    assert state.pending_confirmation_command.command_id == original_pending.command_id
    assert state.pending_confirmation_command.tool_name == "file.write"

    # --- Step 3: runner sees the preserved command and replays it ---
    runner_for_process = _DummyRunner(written)  # fresh runner with same state
    logger = _CapturingLogger()
    tick_ctx = TickRunContext(session_id="sess-bbpc-int", user_input="yes")

    confirmation_process(
        runner=runner_for_process,
        state=state,
        logger=logger,
        tick_ctx=tick_ctx,
    )

    # The affirm path must have:
    # 1. consumed the pending command (cleared on state)
    assert state.pending_confirmation_command is None, (
        "runner affirm path must consume pending command after replay setup"
    )
    # 2. set skip_decide=True so the LLM is not called for `yes`
    assert tick_ctx.skip_decide is True, (
        "runner must skip the decide LLM call when replaying confirmation"
    )
    assert tick_ctx.consume_user_input_for_command is True
    assert tick_ctx.user_input is None
    assert tick_ctx.original_user_input is None
    assert tick_ctx.has_new_user_input is False
    assert state.status == "active"
    assert state.goal == "write a small README"
    assert state.last_user_input == "write a small README"
    # 3. seeded the original tool command on the replay decision
    assert tick_ctx.decision is not None
    seeded_commands = list(getattr(tick_ctx.decision, "_seeded_commands", []) or [])
    assert len(seeded_commands) == 1
    seeded = seeded_commands[0]
    assert seeded.tool_name == "file.write"
    assert seeded.inputs.get("path") == "./tmp-bbpc/README.md"
    # 4. emitted the canonical replay telemetry event
    confirm_events = [
        event for event in logger.events if event[0] == "brain.confirm_replay"
    ]
    assert len(confirm_events) == 1
    assert confirm_events[0][1]["kind"] == "tool"


def test_confirmation_replay_preserves_explicit_tool_reason_for_seeded_replay() -> None:
    inline_state, _ = _build_inline_state_with_pending_file_write()
    inline_state["decision_reason_code"] = "explicit_tool_command"
    state = WorkingState.model_validate(inline_state)
    runner = _DummyRunner(inline_state)
    logger = _CapturingLogger()
    tick_ctx = TickRunContext(session_id="sess-bbpc-explicit", user_input="yes")

    confirmation_process(
        runner=runner,
        state=state,
        logger=logger,
        tick_ctx=tick_ctx,
    )

    assert tick_ctx.decision is not None
    assert tick_ctx.decision.reason_code == "explicit_tool_command"


def test_confirmation_replay_keeps_non_explicit_success_criteria_open() -> None:
    inline_state, _ = _build_inline_state_with_pending_file_write()
    state = WorkingState.model_validate(inline_state)
    runner = _DummyRunner(inline_state)
    logger = _CapturingLogger()
    tick_ctx = TickRunContext(session_id="sess-bbpc-open", user_input="yes")

    confirmation_process(
        runner=runner,
        state=state,
        logger=logger,
        tick_ctx=tick_ctx,
    )

    assert state.plan is not None
    assert dict(state.plan.success_criteria or {}) == {}
    assert list(state.plan.stop_conditions or []) == []


def test_bridge_reset_yes_emits_bbpc_telemetry_event_and_preserves_for_runner() -> None:
    from openminion.services.brain.post_execution.reset import (
        BRAIN_BRIDGE_CONFIRMATION_RESET_PRESERVED_EVENT,
    )

    inline_state, _ = _build_inline_state_with_pending_file_write()
    bridge_runner = _DummyRunner(inline_state)
    bridge = _DummyBridge()
    bridge._reset_state_for_new_input(
        runner=bridge_runner,
        session_id="sess-bbpc-int",
        user_input="yes",
    )

    bbpc_events = [
        event
        for event in bridge_runner.session_api.events
        if event["type"] == BRAIN_BRIDGE_CONFIRMATION_RESET_PRESERVED_EVENT
    ]
    assert len(bbpc_events) == 1
    assert bbpc_events[0]["payload"] == {"reply": "affirm", "command_kind": "tool"}


def test_confirmation_replay_replays_full_confirmed_batch() -> None:
    pending = ToolCommand(
        title="write pyproject",
        tool_name="file.write",
        args={"path": "./tmp-bbpc/pyproject.toml", "body": "[project]\nname='demo'"},
        inputs={"path": "./tmp-bbpc/pyproject.toml", "body": "[project]\nname='demo'"},
        requires_confirmation=True,
    )
    sibling_one = ToolCommand(
        title="write readme",
        tool_name="file.write",
        args={"path": "./tmp-bbpc/README.md", "body": "# demo"},
        inputs={"path": "./tmp-bbpc/README.md", "body": "# demo"},
        requires_confirmation=True,
    )
    sibling_two = ToolCommand(
        title="write module",
        tool_name="file.write",
        args={"path": "./tmp-bbpc/app.py", "body": "print('hi')"},
        inputs={"path": "./tmp-bbpc/app.py", "body": "print('hi')"},
        requires_confirmation=True,
    )
    queued_pending = attach_confirmation_replay_queue(
        pending, [sibling_one, sibling_two]
    )
    state = WorkingState(
        session_id="sess-bbpc-batch",
        agent_id="agent-bbpc-batch",
        trace_id="trace-bbpc-batch",
        budgets_remaining={
            "ticks": 8,
            "tool_calls": 8,
            "a2a_calls": 0,
            "tokens": 100000,
            "time_ms": 45000,
        },
        pending_confirmation_command=queued_pending,
        pending_confirmation_sub_intents=[],
        pending_confirmation_sub_intent_refs=[],
        pending_confirmation_rationale="batch write",
        pending_confirmation_success_criteria={},
        pending_confirmation_feasibility_state={},
        pending_confirmation_feasibility_report=None,
    )
    runner = _DummyRunner(
        {},
        policy_api=_GrantingPolicyAPI(["grant-1", "grant-2", "grant-3"]),
    )
    logger = _CapturingLogger()
    tick_ctx = TickRunContext(session_id="sess-bbpc-batch", user_input="yes")

    confirmation_process(
        runner=runner,
        state=state,
        logger=logger,
        tick_ctx=tick_ctx,
    )

    assert state.status == "active"
    seeded_commands = list(getattr(tick_ctx.decision, "_seeded_commands", []) or [])
    assert [command.args["path"] for command in seeded_commands] == [
        "./tmp-bbpc/pyproject.toml",
        "./tmp-bbpc/README.md",
        "./tmp-bbpc/app.py",
    ]
    assert [
        command.inputs.get("confirmation_grant_id") for command in seeded_commands
    ] == [
        "grant-1",
        "grant-2",
        "grant-3",
    ]
    assert all(
        command.inputs.get("confirmation_source") == "policy_replay"
        for command in seeded_commands
    )
    confirm_events = [
        event for event in logger.events if event[0] == "brain.confirm_replay"
    ]
    assert len(confirm_events) == 1
    assert confirm_events[0][1]["replay_count"] == 3
