from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.llm import LlmctlAdapter, LocalLLMAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.runner import RunnerOptions, BrainRunner
from openminion.modules.llm import ProviderError
from openminion.modules.llm.schemas import LLMResponse, ToolCall, UsageInfo
from openminion.modules.session.capture import (
    RuntimeTerminalCaptureWriter,
    build_capture_identity,
)
from openminion.modules.storage.runtime.migrations import run_migrations
from openminion.modules.storage.runtime.session_store import SessionStore
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
        max_total_llm_tokens=20000,
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
        runner.profile.budgets.max_total_llm_tokens = 20000

        first = runner.run(
            session_id="s-two-turns",
            user_input='tool echo {"msg":"one"}',
        )
        response = LLMResponse(
            ok=True,
            provider="local",
            model="decide-default",
            tool_calls=[
                ToolCall(
                    id="greeting",
                    name="respond",
                    arguments={
                        "answer": "Hi!",
                        "task_update": {
                            "operation": "replace",
                            "expected_revision": 1,
                            "user_quote": "hi",
                            "objective": "Greet the user",
                        },
                    },
                )
            ],
            usage=UsageInfo(total_tokens=1),
        )
        with patch.object(runner.llm_api, "call", return_value=response):
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


def test_failed_provider_turn_persists_typed_outcome() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        failure = ProviderError(
            "empty after recovery",
            code="EMPTY_PROVIDER_RESPONSE",
            details={"api_key": "secret-provider-key", "retry_eligible": False},
        )

        with (
            patch(
                "openminion.modules.brain.runner.coordinator."
                "run_until_idle_runner_lifecycle",
                side_effect=failure,
            ),
            pytest.raises(ProviderError) as raised,
        ):
            runner.run(
                session_id="s-provider-failure",
                user_input="continue",
                trace_id="t-provider-failure",
            )

        assert raised.value is failure
        outcomes = [
            event
            for event in session.list_events("s-provider-failure")
            if event["type"] == "turn.outcome"
        ]
        assert len(outcomes) == 1
        assert outcomes[0]["status"] == "error"
        assert outcomes[0]["payload"]["status"] == "error"
        assert outcomes[0]["payload"]["error"] == {
            "code": "EMPTY_PROVIDER_RESPONSE",
            "message": "empty after recovery",
            "details": {"api_key": "[REDACTED]", "retry_eligible": False},
        }
        assert outcomes[0]["redaction"] == "bounded"


def test_failed_turn_evidence_failure_does_not_mask_provider_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        failure = ProviderError("provider failed", code="PROVIDER_ERROR")

        with (
            patch(
                "openminion.modules.brain.runner.coordinator."
                "run_until_idle_runner_lifecycle",
                side_effect=failure,
            ),
            patch.object(
                session,
                "append_event",
                side_effect=OSError("storage unavailable"),
            ),
            pytest.raises(ProviderError) as raised,
        ):
            runner.run(
                session_id="s-provider-storage-failure",
                user_input="continue",
            )

        assert raised.value is failure


@pytest.mark.parametrize("entrypoint", ["run", "step"])
def test_post_commit_provider_failure_does_not_rewrite_terminal_outcome(
    entrypoint: str,
) -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    sessions = SessionStore(connection)
    sessions.resolve_session(
        agent_id="test-agent",
        channel="console",
        target="local",
        session_id="runtime-session",
    )
    identity = build_capture_identity(
        runtime_session_id="runtime-session",
        root_turn_id="root-turn",
    )
    runner = BrainRunner(
        profile=_profile(),
        session_api=_StorageShapeSessionAPI(),
        options=RunnerOptions(metactl_enabled=False),
        terminal_capture_writer=RuntimeTerminalCaptureWriter(sessions),
    )
    result = StepOutput(
        session_id="brain-session",
        status="done",
        working_state=WorkingState(
            session_id="brain-session",
            agent_id="test-agent",
            trace_id="trace-1",
            runtime_session_id=identity.runtime_session_id,
            root_turn_id=identity.root_turn_id,
            capture_event_id=identity.event_id,
            capture_id=identity.capture_id,
            budgets_remaining=BudgetCounters(
                ticks=1,
                tool_calls=1,
                a2a_calls=0,
                tokens=1,
                time_ms=1,
            ),
        ),
    )
    failure = ProviderError("feedback provider failed", code="PROVIDER_ERROR")
    patch_target = (
        "run_until_idle_runner_lifecycle" if entrypoint == "run" else "run_step"
    )

    with (
        patch(
            f"openminion.modules.brain.runner.coordinator.{patch_target}",
            return_value=result,
        ),
        patch.object(
            runner,
            "_apply_typed_memory_outcome",
            side_effect=failure,
        ),
        pytest.raises(ProviderError) as raised,
    ):
        if entrypoint == "run":
            runner.run(
                session_id="brain-session",
                runtime_session_id=identity.runtime_session_id,
                root_turn_id=identity.root_turn_id,
                capture_event_id=identity.event_id,
                capture_id=identity.capture_id,
            )
        else:
            runner.step(
                session_id="brain-session",
                capture_identity=identity,
            )

    assert raised.value is failure
    stored = sessions.get_event_by_canonical_id(identity.event_id)
    assert stored is not None
    assert stored.payload["status"] == "done"
    assert stored.payload["entrypoint"] == entrypoint


def test_failed_standalone_step_uses_its_trace() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        failure = ProviderError("provider failed", code="PROVIDER_ERROR")

        with (
            patch(
                "openminion.modules.brain.runner.coordinator.run_step",
                side_effect=failure,
            ),
            pytest.raises(ProviderError),
        ):
            runner.step(
                session_id="s-step-failure",
                trace_id="t-step-failure",
            )

        outcomes = [
            event
            for event in session.list_events("s-step-failure")
            if event["type"] == "turn.outcome"
        ]
        assert len(outcomes) == 1
        assert outcomes[0]["trace_id"] == "t-step-failure"


def test_failed_later_tick_uses_enclosing_run_trace() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        state = WorkingState(
            session_id="s-later-failure",
            agent_id="test-agent",
            trace_id="t-run-failure",
            status="continue",
            budgets_remaining=BudgetCounters(
                ticks=2,
                tool_calls=1,
                a2a_calls=0,
                tokens=100,
                time_ms=1000,
            ),
        )
        active = StepOutput(
            session_id="s-later-failure",
            status="continue",
            working_state=state,
        )
        failure = ProviderError("provider failed", code="PROVIDER_ERROR")

        with (
            patch(
                "openminion.modules.brain.runner.coordinator.run_step",
                side_effect=(active, failure),
            ),
            pytest.raises(ProviderError),
        ):
            runner.run(
                session_id="s-later-failure",
                trace_id="t-run-failure",
            )

        outcomes = [
            event
            for event in session.list_events("s-later-failure")
            if event["type"] == "turn.outcome"
        ]
        assert len(outcomes) == 1
        assert outcomes[0]["trace_id"] == "t-run-failure"


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


@pytest.mark.parametrize(
    "provider_failure", [False, True], ids=["malformed-verdict", "provider-error"]
)
def test_closure_failure_after_tool_stops_turn(
    tmp_path: Path, provider_failure: bool
) -> None:
    runner, session = _build_runner(tmp_path)
    original_call = runner.llm_api.call_structured
    client = MagicMock()
    failure = ProviderError(
        "judge unavailable", code="TIMEOUT", details={"request_id": "judge-1"}
    )
    if provider_failure:
        client.call.side_effect = failure
    else:
        client.call.return_value = LLMResponse(
            ok=True,
            provider="test",
            model="judge",
            output_text="",
            tool_calls=[
                ToolCall(name="submit_output", arguments={"final_answer": "Done"})
            ],
            usage=UsageInfo(total_tokens=1),
            finish_reason="tool_calls",
        )
    judge = LlmctlAdapter(client)

    def call_structured(**kwargs):
        if kwargs["schema"].__name__ == "ClosureJudgment":
            return judge.call_structured(**kwargs)
        return original_call(**kwargs)

    with (
        patch.object(runner.llm_api, "call_structured", side_effect=call_structured),
        patch.object(
            runner.tool_api, "execute", wraps=runner.tool_api.execute
        ) as execute,
    ):
        if provider_failure:
            with pytest.raises(ProviderError) as raised:
                runner.run(
                    session_id="closure-failure", user_input='tool echo {"value":"one"}'
                )
            assert raised.value is failure
        else:
            output = runner.run(
                session_id="closure-failure", user_input='tool echo {"value":"one"}'
            )
            assert output.status == "error"
            assert output.action_result.status == "failed"
            assert output.action_result.error.code == "RUNTIME_ERROR"

    assert execute.call_count == 1
    assert client.call.call_count == 1
    events = session.list_events("closure-failure")
    outcomes = [event for event in events if event["type"] == "turn.outcome"]
    assert len(outcomes) == 1
    assert outcomes[0]["payload"]["status"] == "error"
    assert not any(event["type"] == "brain.closure_gate.completed" for event in events)
    if provider_failure:
        assert outcomes[0]["payload"]["error"]["code"] == "TIMEOUT"
        assert outcomes[0]["payload"]["error"]["details"] == {"request_id": "judge-1"}
