from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch


from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.llm import LocalLLMAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.runner import RunnerOptions, BrainRunner
from openminion.modules.brain.schemas import (
    AgentBudgets,
    AgentDefaults,
    BudgetCounters,
    AgentProfile,
    LLMProfiles,
    StepOutput,
    ToolCommand,
    WorkingState,
)
from tests.brain.runner_test_support import build_seeded_act_decision


def _profile() -> AgentProfile:
    budgets = AgentBudgets(
        max_ticks_per_user_turn=5,
        max_tool_calls=3,
        max_a2a_calls=1,
        max_total_llm_tokens=1000,
        max_elapsed_ms=10000,
    )
    llm_profiles = LLMProfiles(
        decide_model="decide-default",
        plan_model="plan-default",
        act_model=None,
        reflect_model="reflect-default",
        summarize_model="summarize-default",
    )
    return AgentProfile(
        agent_id="test-agent",
        role="general",
        llm_profiles=llm_profiles,
        budgets=budgets,
        defaults=AgentDefaults(),
    )


def _build_runner(tmp_path: Path) -> tuple[BrainRunner, LocalSessionStore]:
    session = LocalSessionStore(tmp_path / "sessions")
    runner = BrainRunner(
        profile=_profile(),
        session_api=session,
        context_api=LocalContextAdapter(session_store=session),
        llm_api=LocalLLMAdapter(),
        tool_api=LocalToolAdapter(),
        a2a_api=LocalA2AAdapter(),
        memory_api=LocalMemoryAdapter(tmp_path / "memory"),
        policy_api=LocalPolicyAdapter(),
        options=RunnerOptions(metactl_enabled=False),
    )
    return runner, session


def _build_runner_with_llm_and_tool(
    tmp_path: Path,
    *,
    llm_api: LocalLLMAdapter,
    tool_api: LocalToolAdapter,
) -> tuple[BrainRunner, LocalSessionStore]:
    session = LocalSessionStore(tmp_path / "sessions")
    runner = BrainRunner(
        profile=_profile(),
        session_api=session,
        context_api=LocalContextAdapter(session_store=session),
        llm_api=llm_api,
        tool_api=tool_api,
        a2a_api=LocalA2AAdapter(),
        memory_api=LocalMemoryAdapter(tmp_path / "memory"),
        policy_api=LocalPolicyAdapter(),
        options=RunnerOptions(metactl_enabled=False),
    )
    return runner, session


def _build_runner_with_tool(
    tmp_path: Path, *, tool_api: LocalToolAdapter
) -> tuple[BrainRunner, LocalSessionStore]:
    session = LocalSessionStore(tmp_path / "sessions")
    runner = BrainRunner(
        profile=_profile(),
        session_api=session,
        context_api=LocalContextAdapter(session_store=session),
        llm_api=LocalLLMAdapter(),
        tool_api=tool_api,
        a2a_api=LocalA2AAdapter(),
        memory_api=LocalMemoryAdapter(tmp_path / "memory"),
        policy_api=LocalPolicyAdapter(),
        options=RunnerOptions(metactl_enabled=False),
    )
    return runner, session


def _index(types: list[str], name: str) -> int:
    return types.index(name)


def _tool_command(
    *,
    tool_name: str,
    args: dict[str, Any],
    title: str | None = None,
    idempotency_key: str | None = None,
) -> ToolCommand:
    return ToolCommand(
        title=title or f"Tool call: {tool_name}",
        tool_name=tool_name,
        args=args,
        success_criteria={"status": "success"},
        idempotency_key=idempotency_key,
    )


def _seeded_multi_command_decision(*, reason_code: str, commands: list[ToolCommand]):
    decision = build_seeded_act_decision(
        reason_code=reason_code,
        act_profile="general",
        execution_target={"kind": "local"},
        command=commands[0],
    )
    decision._seeded_commands = [command.model_copy(deep=True) for command in commands]
    return decision


class _StorageShapeSessionAPI:
    def __init__(self) -> None:
        self.outcomes: list[dict[str, Any]] = []

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        del session_id
        return [
            {
                "event_type": "tool.request",
                "trace_id": "t-storage",
                "payload": {
                    "command_id": "cmd-storage",
                    "tool_name": "web.search",
                },
            },
            {
                "event_type": "tool.completed",
                "trace_id": "t-storage",
                "payload": {
                    "command_id": "cmd-storage",
                    "tool_name": "web.search",
                    "status": "success",
                },
            },
        ]

    def append_event(
        self,
        session_id: str,
        type: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> str:
        del kwargs
        self.outcomes.append(
            {
                "session_id": session_id,
                "type": type,
                "payload": payload,
            }
        )
        return "evt-storage"


def test_emit_turn_outcome_counts_storage_shaped_events() -> None:
    runner = BrainRunner(
        profile=_profile(),
        session_api=_StorageShapeSessionAPI(),
        options=RunnerOptions(metactl_enabled=False),
    )
    state = WorkingState(
        session_id="s-storage",
        agent_id="test-agent",
        active_mode_name="act",
        trace_id="t-storage",
        budgets_remaining=BudgetCounters(
            ticks=1,
            tool_calls=1,
            a2a_calls=0,
            tokens=1,
            time_ms=1,
        ),
    )
    result = StepOutput(
        session_id="s-storage",
        status="done",
        working_state=state,
    )

    runner._emit_turn_outcome(
        session_id="s-storage",
        result=result,
        entrypoint="run",
    )

    outcomes = runner.session_api.outcomes
    assert len(outcomes) == 1
    assert outcomes[0]["type"] == "turn.outcome"
    assert outcomes[0]["payload"]["tool_request_count"] == 1
    assert outcomes[0]["payload"]["tool_completed_count"] == 1


def test_new_user_turn_gets_fresh_trace_and_outcome_counts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))

        first = runner.run(
            session_id="s-two-turns",
            user_input='tool echo {"msg":"one"}',
        )
        second = runner.run(
            session_id="s-two-turns",
            user_input="hi",
        )

        assert first.status == "done"
        assert second.status == "done"

        events = session.list_events("s-two-turns")
        outcomes = [event for event in events if event["type"] == "turn.outcome"]
        assert len(outcomes) == 2
        assert outcomes[0]["trace_id"] != outcomes[1]["trace_id"]
        assert outcomes[0]["payload"]["tool_request_count"] >= 1
        assert outcomes[0]["payload"]["tool_completed_count"] >= 1
        assert outcomes[1]["payload"]["tool_request_count"] == 0
        assert outcomes[1]["payload"]["tool_completed_count"] == 0


def test_seeded_act_flow_emits_public_act_outcome() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        output = runner.run(
            session_id="s-act-single",
            user_input='tool echo {"value":"one"}',
            trace_id="t-act-single",
        )

        assert output.status == "done"
        assert output.working_state.active_workflow_name is None
        assert output.working_state.active_workflow_kind is None

        events = session.list_events("s-act-single")
        tool_requests = [event for event in events if event["type"] == "tool.request"]
        assert len(tool_requests) == 1
        assert tool_requests[0]["payload"]["tool_name"] == "echo"
        assert tool_requests[0]["payload"]["mode_name"] == "act"

        outcomes = [event for event in events if event["type"] == "turn.outcome"]
        assert len(outcomes) == 1
        payload = outcomes[0]["payload"]
        assert payload["mode_name"] == "act"
        assert "workflow_name" in payload
        assert "workflow_kind" in payload
        assert payload["workflow_name"] is None
        assert payload["workflow_kind"] is None
        assert payload["tool_request_count"] == 1
        assert payload["tool_completed_count"] == 1


def test_seeded_multi_command_flow_emits_public_act_outcome() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner_with_llm_and_tool(
            Path(tmp),
            llm_api=LocalLLMAdapter(),
            tool_api=LocalToolAdapter(),
        )
        decision = _seeded_multi_command_decision(
            reason_code="generic_multi_command",
            commands=[
                _tool_command(
                    tool_name="echo",
                    args={"value": "first"},
                    title="Echo first value",
                    idempotency_key="generic-multi-echo",
                ),
                _tool_command(
                    tool_name="create_artifact",
                    args={"name": "proof.txt"},
                    title="Create artifact",
                    idempotency_key="generic-multi-artifact",
                ),
            ],
        )
        with patch.object(runner, "_decide", return_value=decision):
            output = runner.run(
                session_id="s-generic-multi-command",
                user_input="echo a value and create an artifact",
                trace_id="t-generic-multi-command",
            )

        assert output.status == "done"
        assert output.working_state.active_workflow_name is None
        assert output.working_state.active_workflow_kind is None

        events = session.list_events("s-generic-multi-command")
        types = [event["type"] for event in events]
        assert "brain.workflow.compiled.selected" not in types
        assert "brain.workflow.compiled.completed" not in types

        tool_requests = [event for event in events if event["type"] == "tool.request"]
        assert [event["payload"]["tool_name"] for event in tool_requests] == [
            "echo",
            "create_artifact",
        ]
        assert len({event["payload"]["command_id"] for event in tool_requests}) == 2

        outcomes = [event for event in events if event["type"] == "turn.outcome"]
        assert len(outcomes) == 1
        payload = outcomes[0]["payload"]
        assert payload["mode_name"] == "act"
        assert payload["workflow_name"] is None
        assert payload["workflow_kind"] is None
        assert payload["tool_request_count"] == 2
        assert payload["tool_completed_count"] == 2


def test_seeded_compound_time_flow_emits_public_act_outcome_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner_with_llm_and_tool(
            Path(tmp),
            llm_api=LocalLLMAdapter(),
            tool_api=LocalToolAdapter(),
        )
        decision = _seeded_multi_command_decision(
            reason_code="compound_time_query",
            commands=[
                _tool_command(
                    tool_name="time",
                    args={"timezone": "Asia/Tokyo"},
                    title="Tokyo time",
                    idempotency_key="seeded-time-tokyo",
                ),
                _tool_command(
                    tool_name="time",
                    args={"timezone": "America/New_York"},
                    title="New York time",
                    idempotency_key="seeded-time-nyc",
                ),
            ],
        )
        with patch.object(runner, "_decide", return_value=decision):
            output = runner.run(
                session_id="s-seeded-compound-time",
                user_input="what time is it in tokyo and new york?",
                trace_id="t-seeded-compound-time",
            )

        assert output.status == "done"
        assert output.working_state.active_workflow_name is None
        assert output.working_state.active_workflow_kind is None

        events = session.list_events("s-seeded-compound-time")
        types = [event["type"] for event in events]
        assert "brain.workflow.compiled.selected" not in types
        assert "brain.workflow.compiled.completed" not in types

        outcomes = [event for event in events if event["type"] == "turn.outcome"]
        assert len(outcomes) == 1
        payload = outcomes[0]["payload"]
        assert payload["mode_name"] == "act"
        assert payload["workflow_name"] is None
        assert payload["workflow_kind"] is None
        assert payload["tool_request_count"] == 2
        assert payload["tool_completed_count"] == 2


class _PollingSuccessToolAdapter(LocalToolAdapter):
    def execute(self, *, command: dict, session_id: str, trace_id: str) -> dict:
        del command, session_id, trace_id
        return {
            "status": "pending",
            "task_id": "job-success-1",
            "poll_after_ms": 1,
            "summary": "Async job created",
        }

    def poll_task(self, *, task_id: str, session_id: str, trace_id: str) -> dict:
        del task_id, session_id, trace_id
        return {
            "status": "completed",
            "summary": "Async job finished",
            "outputs": {"ok": True},
        }


class _PollingFailureToolAdapter(LocalToolAdapter):
    def execute(self, *, command: dict, session_id: str, trace_id: str) -> dict:
        del command, session_id, trace_id
        return {
            "status": "pending",
            "task_id": "job-failed-1",
            "poll_after_ms": 1,
            "summary": "Async job created",
        }

    def poll_task(self, *, task_id: str, session_id: str, trace_id: str) -> dict:
        del task_id, session_id, trace_id
        return {
            "status": "failed",
            "summary": "Async job failed",
            "error": {"code": "ASYNC_FAILED", "message": "job failed"},
        }


def test_async_job_pending_transitions_to_completed_with_polling() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner_with_tool(
            Path(tmp), tool_api=_PollingSuccessToolAdapter()
        )
        output = runner.run(
            session_id="s-async-complete",
            user_input='tool sleep_async {"delay_ms": 1}',
            trace_id="t-async-complete",
        )
        assert output.status in {"active", "done", "job_pending"}
        events = session.list_events("s-async-complete")
        types = [event["type"] for event in events]
        assert "tool.request" in types
        assert output.status == "job_pending"


def test_async_job_pending_transitions_to_failed_with_polling() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner_with_tool(
            Path(tmp), tool_api=_PollingFailureToolAdapter()
        )
        output = runner.run(
            session_id="s-async-failed",
            user_input='tool sleep_async {"delay_ms": 1}',
            trace_id="t-async-failed",
        )
        assert output.status in {"waiting_user", "error", "job_pending"}
        events = session.list_events("s-async-failed")
        types = [event["type"] for event in events]
        assert "tool.request" in types
        assert output.status == "job_pending"
