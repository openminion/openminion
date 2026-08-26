from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_BLOCKED,
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_COMMAND_KIND_AGENT,
    BRAIN_COMMAND_KIND_TOOL,
    BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE,
)
from openminion.modules.brain.schemas import (
    ActionResult,
    AgentCommand,
    ArtifactRef,
    BudgetCounters,
    RequestReadiness,
    ToolCommand,
    WorkingState,
)
from openminion.modules.brain.tools.action_dispatch import (
    execute_action_dispatch,
)
from openminion.modules.brain.tools.lifecycle import (
    LIFECYCLE_EVENT_ON_SUBAGENT_STOP,
    get_default_lifecycle_registry,
    reset_default_lifecycle_registry,
)


def _make_state(*, permission_mode: str = "default") -> WorkingState:
    state = WorkingState(
        session_id="s-readonly-gate",
        agent_id="agent",
        budgets_remaining=BudgetCounters(
            ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
        ),
    )
    state.permission_mode = permission_mode
    return state


def _make_command(
    *, tool_name: str, command_id: str = "cmd-1", args: dict[str, Any] | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        kind=BRAIN_COMMAND_KIND_TOOL,
        command_id=command_id,
        idempotency_key=f"idem-{command_id}",
        tool_name=tool_name,
        title=f"tool:{tool_name}",
        args=args or {},
    )


def _make_runner() -> SimpleNamespace:
    return SimpleNamespace(
        safety_api=None,
        options=SimpleNamespace(idempotency_enabled=False),
        tool_api=None,
        a2a_api=None,
        _emit_brain_operation=lambda **kwargs: True,
        _emit_tool_progress_event=None,
        _remember_idempotency=lambda **kwargs: None,
        _validate_tool_args=lambda **kwargs: None,
        _budget_blocked_result=lambda **kwargs: None,
        _normalize_execution_result=lambda **kwargs: (None, None),
    )


# ── readonly mode blocks write-capable tools ───────────────────────


@pytest.mark.parametrize(
    "tool_name",
    [
        "file.write",
        "file.edit",
        "code.patch",
        "exec.run",
        "git.commit",
        "memory.write",
        "task.schedule",
        "skill.ingest",
        "tool.author",
    ],
)
def test_readonly_blocks_write_tools(tool_name: str) -> None:
    state = _make_state(permission_mode="readonly")
    command = _make_command(tool_name=tool_name)
    runner = _make_runner()
    logger = SimpleNamespace(emit=lambda *a, **k: None)
    result, job = execute_action_dispatch(
        runner,
        state=state,
        command=command,
        logger=logger,
        sanitize_tool_command_args=lambda runner, command: ({}, []),
        execute_action_fn=None,
    )
    assert job is None
    assert result.status == BRAIN_ACTION_STATUS_BLOCKED
    assert result.error.code == "PERMISSION_DENIED_READONLY"
    assert result.error.details["reason_code"] == "readonly_blocks_write"
    assert result.error.details["tool_name"] == tool_name
    assert result.error.details["permission_mode"] == "readonly"
    assert "shift+tab" in result.error.message or "/permissions" in result.error.message


# ── other modes do NOT short-circuit the dispatch at the gate ────


@pytest.mark.parametrize("mode", ["default", "bypass"])
def test_non_readonly_modes_skip_the_gate(mode: str) -> None:
    state = _make_state(permission_mode=mode)
    command = _make_command(tool_name="file.write")
    runner = _make_runner()
    # Stub tool_api so the gate is the only short-circuit point.
    runner.tool_api = SimpleNamespace(
        execute=lambda command, session_id, trace_id: {
            "status": "success",
            "summary": "ok",
            "artifact_refs": [],
            "memory_refs": [],
        }
    )
    logger = SimpleNamespace(emit=lambda *a, **k: None)
    with pytest.raises(AttributeError, match="model_dump"):
        execute_action_dispatch(
            runner,
            state=state,
            command=command,
            logger=logger,
            sanitize_tool_command_args=lambda runner, command: ({}, []),
            execute_action_fn=None,
        )


def test_readonly_mode_with_read_tool_skips_the_gate() -> None:
    state = _make_state(permission_mode="readonly")
    command = _make_command(tool_name="file.read")  # read-only — gate skips
    runner = _make_runner()
    runner.tool_api = SimpleNamespace(
        execute=lambda command, session_id, trace_id: {
            "status": "success",
            "summary": "ok",
            "artifact_refs": [],
            "memory_refs": [],
        }
    )
    logger = SimpleNamespace(emit=lambda *a, **k: None)
    with pytest.raises(AttributeError, match="model_dump"):
        execute_action_dispatch(
            runner,
            state=state,
            command=command,
            logger=logger,
            sanitize_tool_command_args=lambda runner, command: ({}, []),
            execute_action_fn=None,
        )


def test_non_execution_outcome_blocks_write_even_with_bypass() -> None:
    state = _make_state(permission_mode="bypass")
    state.request_readiness = RequestReadiness(
        posture="direct",
        requested_outcome="answer_only",
        state="ready",
    )
    command = _make_command(tool_name="file.write")
    runner = _make_runner()
    logger = SimpleNamespace(emit=lambda *a, **k: None)

    result, job = execute_action_dispatch(
        runner,
        state=state,
        command=command,
        logger=logger,
        sanitize_tool_command_args=lambda runner, command: ({}, []),
        execute_action_fn=None,
    )

    assert job is None
    assert result.status == BRAIN_ACTION_STATUS_BLOCKED
    assert result.error.code == "REQUEST_OUTCOME_EFFECT_BLOCKED"
    assert result.error.details["requested_outcome"] == "answer_only"


def test_plan_only_allows_session_plan_control_exception() -> None:
    state = _make_state(permission_mode="readonly")
    state.request_readiness = RequestReadiness(
        posture="brief_plan",
        requested_outcome="plan_only",
        state="ready",
    )
    command = _make_command(tool_name="plan.set")
    runner = _make_runner()
    runner.tool_api = SimpleNamespace(
        execute=lambda command, session_id, trace_id: {
            "status": "success",
            "summary": "ok",
            "artifact_refs": [],
            "memory_refs": [],
        }
    )
    logger = SimpleNamespace(emit=lambda *a, **k: None)

    with pytest.raises(AttributeError, match="model_dump"):
        execute_action_dispatch(
            runner,
            state=state,
            command=command,
            logger=logger,
            sanitize_tool_command_args=lambda runner, command: ({}, []),
            execute_action_fn=None,
        )


@pytest.mark.parametrize(
    ("mode", "args"),
    [
        ("sync", {"agent_id": "researcher", "instruction": "map the codebase"}),
        ("async", {"agent_id": "researcher", "instruction": "map the codebase"}),
        ("status", {"task_id": "task-1"}),
        ("resume", {"task_id": "task-1"}),
        ("cancel", {"task_id": "task-1"}),
        (
            "accept",
            {"child_artifact": {"artifact_id": "a1"}, "workspace_root": "/repo"},
        ),
        ("reject", {"child_artifact": {"artifact_id": "a1"}}),
    ],
)
def test_task_delegate_tool_dispatches_through_canonical_tool_path(
    mode: str, args: dict[str, object]
) -> None:
    state = _make_state(permission_mode="default")
    delegate_args = {"mode": mode, **args}
    command = ToolCommand(
        kind=BRAIN_COMMAND_KIND_TOOL,
        command_id="delegate-1",
        title="Tool call: task.delegate",
        tool_name="task.delegate",
        args=delegate_args,
        idempotency_key="idem-delegate-1",
    )
    calls: dict[str, object] = {}

    def _execute(**kwargs):
        calls.update(kwargs)
        return {"status": BRAIN_ACTION_STATUS_SUCCESS, "summary": "delegated ok"}

    runner = _make_runner()
    runner.tool_api = SimpleNamespace(execute=_execute)
    runner._normalize_execution_result = lambda **kwargs: (
        ActionResult(
            command_id=kwargs["command_id"],
            status=BRAIN_ACTION_STATUS_SUCCESS,
            summary=kwargs["raw"]["summary"],
        ),
        None,
    )
    logger = SimpleNamespace(emit=lambda *a, **k: None)

    result, job = execute_action_dispatch(
        runner,
        state=state,
        command=command,
        logger=logger,
        sanitize_tool_command_args=lambda runner, command: ({}, []),
        execute_action_fn=None,
    )

    assert job is None
    assert result.status == BRAIN_ACTION_STATUS_SUCCESS
    assert calls["session_id"] == state.session_id
    payload = calls["command"]
    assert payload["kind"] == "tool"
    assert payload["tool_name"] == "task.delegate"
    assert payload["args"] == delegate_args


def test_per_tool_readonly_override_blocks_write_when_global_default() -> None:
    state = _make_state(permission_mode="default")
    state.permission_overrides = {"file.write": "readonly"}
    command = _make_command(tool_name="file.write")
    runner = _make_runner()
    logger = SimpleNamespace(emit=lambda *a, **k: None)

    result, job = execute_action_dispatch(
        runner,
        state=state,
        command=command,
        logger=logger,
        sanitize_tool_command_args=lambda runner, command: ({}, []),
        execute_action_fn=None,
    )

    assert job is None
    assert result.status == BRAIN_ACTION_STATUS_BLOCKED
    assert result.error.code == "PERMISSION_DENIED_READONLY"
    assert result.error.details["tool_name"] == "file.write"


def test_per_tool_bypass_override_wins_over_global_readonly() -> None:
    state = _make_state(permission_mode="readonly")
    state.permission_overrides = {"file.write": "bypass"}
    command = ToolCommand(
        kind=BRAIN_COMMAND_KIND_TOOL,
        command_id="write-1",
        title="Tool call: file.write",
        tool_name="file.write",
        args={"path": "x.txt", "content": "ok"},
        idempotency_key="idem-write-1",
    )
    calls: dict[str, object] = {}

    def _execute(**kwargs):
        calls.update(kwargs)
        return {"status": BRAIN_ACTION_STATUS_SUCCESS, "summary": "wrote"}

    runner = _make_runner()
    runner.tool_api = SimpleNamespace(execute=_execute)
    runner._normalize_execution_result = lambda **kwargs: (
        ActionResult(
            command_id=kwargs["command_id"],
            status=BRAIN_ACTION_STATUS_SUCCESS,
            summary=kwargs["raw"]["summary"],
        ),
        None,
    )
    logger = SimpleNamespace(emit=lambda *a, **k: None)

    result, job = execute_action_dispatch(
        runner,
        state=state,
        command=command,
        logger=logger,
        sanitize_tool_command_args=lambda runner, command: ({}, []),
        execute_action_fn=None,
    )

    assert job is None
    assert result.status == BRAIN_ACTION_STATUS_SUCCESS
    payload = calls["command"]
    assert payload["inputs"]["permission_mode"] == "bypass"


def test_tool_progress_observer_failure_is_logged_and_counted() -> None:
    state = _make_state(permission_mode="default")
    state.trace_id = "trace-observer"
    command = ToolCommand(
        kind=BRAIN_COMMAND_KIND_TOOL,
        command_id="tool-observer-1",
        title="Tool call: file.read",
        tool_name="file.read",
        args={"path": "README.md"},
        idempotency_key="idem-tool-observer-1",
    )
    operations: list[dict[str, Any]] = []

    def _emit_progress(**kwargs):
        del kwargs
        raise RuntimeError("synthetic progress sink failure")

    def _execute(**kwargs):
        del kwargs
        return {"status": BRAIN_ACTION_STATUS_SUCCESS, "summary": "read"}

    runner = _make_runner()
    runner.tool_api = SimpleNamespace(execute=_execute)
    runner._emit_tool_progress_event = _emit_progress
    runner._emit_brain_operation = lambda **kwargs: operations.append(kwargs) or True
    runner._normalize_execution_result = lambda **kwargs: (
        ActionResult(
            command_id=kwargs["command_id"],
            status=BRAIN_ACTION_STATUS_SUCCESS,
            summary=kwargs["raw"]["summary"],
        ),
        None,
    )
    logger = SimpleNamespace(emit=lambda *a, **k: None)

    result, job = execute_action_dispatch(
        runner,
        state=state,
        command=command,
        logger=logger,
        sanitize_tool_command_args=lambda runner, command: (dict(command.args), []),
        execute_action_fn=None,
    )

    assert job is None
    assert result.status == BRAIN_ACTION_STATUS_SUCCESS
    failures = [
        item
        for item in operations
        if item.get("operation") == "dispatch_observer_failure"
    ]
    assert failures
    assert {item["extra"]["observer"] for item in failures} == {
        "tool_progress_started",
        "tool_progress_completed",
    }


def test_subagent_stop_lifecycle_event_fires_after_a2a_completion() -> None:
    reset_default_lifecycle_registry()
    events = []
    get_default_lifecycle_registry().register(
        LIFECYCLE_EVENT_ON_SUBAGENT_STOP,
        lambda event, context: events.append(event),
    )
    state = _make_state(permission_mode="default")
    state.trace_id = "trace-subagent"
    command = AgentCommand(
        kind=BRAIN_COMMAND_KIND_AGENT,
        command_id="agent-1",
        title="Delegate to researcher",
        target_agent_id="researcher",
        method="delegate",
        params={"instruction": "map repo"},
        idempotency_key="idem-agent-1",
    )
    runner = _make_runner()
    runner.a2a_api = SimpleNamespace(
        call=lambda **kwargs: {
            "status": BRAIN_ACTION_STATUS_SUCCESS,
            "summary": "done",
        }
    )
    runner._normalize_execution_result = lambda **kwargs: (
        ActionResult(
            command_id=kwargs["command_id"],
            status=BRAIN_ACTION_STATUS_SUCCESS,
            summary=kwargs["raw"]["summary"],
        ),
        None,
    )
    logger = SimpleNamespace(emit=lambda *a, **k: None)

    result, job = execute_action_dispatch(
        runner,
        state=state,
        command=command,
        logger=logger,
        sanitize_tool_command_args=lambda runner, command: ({}, []),
        execute_action_fn=None,
    )

    assert job is None
    assert result.status == BRAIN_ACTION_STATUS_SUCCESS
    assert len(events) == 1
    assert events[0].event_type == LIFECYCLE_EVENT_ON_SUBAGENT_STOP
    assert events[0].subagent_id == "researcher"
    assert events[0].tool_ok is True
    reset_default_lifecycle_registry()


def test_a2a_completion_events_include_typed_parent_child_lineage() -> None:
    state = _make_state(permission_mode="default")
    state.trace_id = "trace-lineage"
    state.active_mode_name = BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE
    state.active_workflow_name = "repo-review"
    state.active_workflow_kind = "orchestrate"
    command = AgentCommand(
        kind=BRAIN_COMMAND_KIND_AGENT,
        command_id="agent-lineage-1",
        title="Delegate to reviewer",
        target_agent_id="reviewer",
        method="delegate",
        params={"instruction": "review docs"},
        idempotency_key="idem-agent-lineage-1",
    )
    emitted: list[tuple[str, dict[str, object], dict[str, object]]] = []
    operations: list[dict[str, object]] = []
    runner = _make_runner()
    runner.a2a_api = SimpleNamespace(
        call=lambda **kwargs: {
            "status": BRAIN_ACTION_STATUS_SUCCESS,
            "summary": "review complete",
        }
    )
    runner._emit_brain_operation = lambda **kwargs: operations.append(kwargs) or True
    runner._normalize_execution_result = lambda **kwargs: (
        ActionResult(
            command_id=kwargs["command_id"],
            status=BRAIN_ACTION_STATUS_SUCCESS,
            summary=kwargs["raw"]["summary"],
            artifact_refs=[ArtifactRef(ref="artifact://delegate/review")],
            memory_refs=["memory://delegate/review"],
        ),
        None,
    )
    logger = SimpleNamespace(
        emit=lambda event, payload, **kwargs: emitted.append(
            (event, dict(payload), dict(kwargs))
        )
    )

    result, job = execute_action_dispatch(
        runner,
        state=state,
        command=command,
        logger=logger,
        sanitize_tool_command_args=lambda runner, command: ({}, []),
        execute_action_fn=None,
    )

    assert job is None
    assert result.status == BRAIN_ACTION_STATUS_SUCCESS
    request_payload = next(
        payload for event, payload, _kwargs in emitted if event == "a2a.request"
    )
    completed_payload, completed_kwargs = next(
        (payload, kwargs)
        for event, payload, kwargs in emitted
        if event == "a2a.completed"
    )
    assert request_payload["target_agent_id"] == "reviewer"
    assert request_payload["method"] == "delegate"
    assert request_payload["command_id"] == "agent-lineage-1"
    assert request_payload["command_kind"] == BRAIN_COMMAND_KIND_AGENT
    assert request_payload["mode_name"] == "act"
    assert request_payload["decision_mode"] == "act"
    assert request_payload["act_profile"] == "orchestrate"
    assert request_payload["workflow_name"] == "repo-review"
    assert request_payload["workflow_kind"] == "orchestrate"
    assert completed_payload["summary"] == "review complete"
    assert completed_payload["command_id"] == request_payload["command_id"]
    assert completed_kwargs["trace_id"] == "trace-lineage"
    assert completed_kwargs["artifact_refs"] == ["artifact://delegate/review"]
    assert completed_kwargs["memory_refs"] == ["memory://delegate/review"]
    assert operations == [
        {
            "session_id": state.session_id,
            "turn_id": "trace-lineage",
            "operation": "tool_loop",
            "extra": {"provider": "a2a", "target_agent_id": "reviewer"},
        }
    ]


def test_subagent_stop_lifecycle_event_fires_for_unavailable_a2a() -> None:
    reset_default_lifecycle_registry()
    events = []
    get_default_lifecycle_registry().register(
        LIFECYCLE_EVENT_ON_SUBAGENT_STOP,
        lambda event, context: events.append(event),
    )
    state = _make_state(permission_mode="default")
    command = AgentCommand(
        kind=BRAIN_COMMAND_KIND_AGENT,
        command_id="agent-2",
        title="Delegate to reviewer",
        target_agent_id="reviewer",
        method="delegate",
        params={"instruction": "review"},
        idempotency_key="idem-agent-2",
    )
    runner = _make_runner()
    logger = SimpleNamespace(emit=lambda *a, **k: None)

    result, job = execute_action_dispatch(
        runner,
        state=state,
        command=command,
        logger=logger,
        sanitize_tool_command_args=lambda runner, command: ({}, []),
        execute_action_fn=None,
    )

    assert job is None
    assert result.status == BRAIN_ACTION_STATUS_FAILED
    assert len(events) == 1
    assert events[0].subagent_id == "reviewer"
    assert events[0].tool_ok is False
    reset_default_lifecycle_registry()
