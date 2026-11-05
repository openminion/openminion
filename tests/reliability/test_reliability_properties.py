"""Reliability properties: stopping rules, retry bounds, handoff, rollback, budgets.

Each class pins one property in both directions where the property is two-sided
(stop too early / never stop, retry / never retry, resume / do not claim success).
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace


from openminion.base.types import Message
from openminion.modules.brain.loop.tools.response_payloads import (
    _delegation_result_summary_payload,
)
from openminion.modules.context.budget import (
    ContextBudgetConfig,
    assemble_budgeted_context,
)
from openminion.modules.llm.providers.base import (
    ProviderResponse,
    ProviderToolCall,
)
from openminion.modules.llm.runtime import flow as llm_flow
from openminion.modules.tool.base import ToolExecutionResult
from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.services.agent.constants import (
    TERMINATION_REASON_LOOP_NO_PROGRESS,
    TERMINATION_REASON_TOOL_LOOP_MAX_STEPS,
)
from openminion.services.agent.execution.unforced.loop import handle_unforced_tool_calls
from tests.services.agent.test_unforced_lane_recovery import _FakeRuntimeOps, _deps


def _tool_call_response(tool: str = "file.write", *, step: int = 0) -> ProviderResponse:
    return ProviderResponse(
        text="",
        model="fake-model",
        tool_calls=[
            ProviderToolCall(
                id=f"call-{step}",
                name=tool,
                arguments={"path": f"file-{step}.txt", "content": f"step {step}"},
            )
        ],
    )


def _ok_batch(tool: str = "file.write") -> ToolExecutionBatch:
    return ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name=tool,
                ok=True,
                verified=True,
                content="written",
                error="",
                data={},
                source="native",
            )
        ]
    )


def _runner(runtime_ops, *, max_steps: int):
    return SimpleNamespace(
        runtime_ops=runtime_ops,
        runtime=SimpleNamespace(
            inbound=Message(channel="console", target="cli", body="do work"),
            system_prompt="system",
            provider_history=[],
            user_message="do work",
        ),
        service_port=SimpleNamespace(
            config=SimpleNamespace(
                runtime=SimpleNamespace(agent_loop_max_steps=max_steps)
            )
        ),
    )


def _run_loop(runtime_ops, *, max_steps: int, initial):
    """Drive the loop with a distinct initial response and continuation responses."""
    return asyncio.run(
        handle_unforced_tool_calls(
            _runner(runtime_ops, max_steps=max_steps),
            initial_response=initial,
            intent_category="coding",
            tool_call_strategy="auto",
            tool_budget_state=None,
            deps=_deps(),
        )
    )


class TestStoppingRuleCorrectness:
    def test_productive_loop_executes_the_whole_step_budget(self) -> None:
        """Must not stop early: every allowed step executes its tool batch."""
        max_steps = 4
        runtime_ops = _FakeRuntimeOps(
            execute_batches=[(_ok_batch(), [], False) for _ in range(max_steps)],
            provider_responses=[_tool_call_response(step=i) for i in range(1, 10)],
        )

        _run_loop(
            runtime_ops,
            max_steps=max_steps,
            initial=_tool_call_response(step=0),
        )

        # Step 0 is the separate initial response; with responses to spare the loop
        # still executes exactly its budget of batches and stops on the cap.
        assert runtime_ops.provider_index == max_steps
        assert runtime_ops.execute_index == max_steps

    def test_step_cap_is_never_exceeded(self) -> None:
        """Must not loop indefinitely: the cap bounds model calls and executions."""
        cap = 3
        runtime_ops = _FakeRuntimeOps(
            execute_batches=[(_ok_batch(), [], False) for _ in range(10)],
            provider_responses=[_tool_call_response(step=i) for i in range(1, 10)],
        )

        result = _run_loop(
            runtime_ops,
            max_steps=cap,
            initial=_tool_call_response(step=0),
        )

        assert runtime_ops.provider_index == cap
        assert runtime_ops.execute_index == cap
        assert result.metadata["tool_loop_termination_reason"] == (
            TERMINATION_REASON_TOOL_LOOP_MAX_STEPS
        )

    def _denied_batch(self, call_id: str, *, suggestion: bool) -> ToolExecutionBatch:
        details = (
            {
                "suggested_tool": "file.list_dir",
                "suggested_fix": "Use file.list_dir and file.read instead.",
            }
            if suggestion
            else {}
        )
        return ToolExecutionBatch(
            results=[
                ToolExecutionResult(
                    tool_name="exec.run",
                    ok=False,
                    verified=False,
                    content="",
                    error="security_deny",
                    data={
                        "error_code": "POLICY_DENIED",
                        "error": {"code": "POLICY_DENIED", "details": details},
                    },
                    call_id=call_id,
                    source="policy",
                )
            ]
        )

    def test_repeated_recoverable_failure_reaches_the_stall_rule(self) -> None:
        """A recoverable failure that repeats stops at the threshold, not the cap."""
        runtime_ops = _FakeRuntimeOps(
            execute_batches=[
                (self._denied_batch("call-1", suggestion=True), [], True),
                (self._denied_batch("call-2", suggestion=True), [], True),
            ],
            provider_responses=[_tool_call_response(step=i) for i in range(1, 4)],
        )

        result = _run_loop(
            runtime_ops,
            max_steps=8,
            initial=_tool_call_response(step=0),
        )

        assert result.metadata["tool_loop_termination_reason"] == (
            TERMINATION_REASON_LOOP_NO_PROGRESS
        )
        assert runtime_ops.execute_index == 2
        assert runtime_ops.provider_index == 1

    def test_immediate_denial_is_its_own_control(self) -> None:
        """A denial with no recovery hint stops at once; it is not a stall."""
        runtime_ops = _FakeRuntimeOps(
            execute_batches=[
                (self._denied_batch("call-1", suggestion=False), [], True),
            ],
            provider_responses=[_tool_call_response(step=1)],
        )

        result = _run_loop(
            runtime_ops,
            max_steps=8,
            initial=_tool_call_response(step=0),
        )

        assert result.metadata["tool_loop_termination_reason"] != (
            TERMINATION_REASON_LOOP_NO_PROGRESS
        )
        assert runtime_ops.execute_index == 1


class TestRetryPolicy:
    def test_only_retryable_codes_are_retried(self) -> None:
        assert {"RATE_LIMITED", "TIMEOUT", "PROVIDER_ERROR"} <= llm_flow.RETRYABLE_CODES
        for non_retryable in ("INVALID_ARGUMENT", "AUTH_ERROR", "POLICY_BLOCKED"):
            assert non_retryable not in llm_flow.RETRYABLE_CODES

    def _call(self, monkeypatch, *, error_code: str, max_retries: int):
        sleeps: list[float] = []
        attempts: list[int] = []

        class _Client:
            def _retry_config(self, overrides):
                return max_retries, 300

            def _emit_operation(self, **kwargs):
                return None

            llmctl = SimpleNamespace(registry=SimpleNamespace(get=lambda name: name))

        def _fake_complete(client, **_kwargs):
            attempts.append(1)
            return SimpleNamespace(
                ok=False,
                latency_ms=1,
                error=SimpleNamespace(code=error_code, message="boom"),
            )

        monkeypatch.setattr(
            llm_flow, "_provider_call_request_and_config", lambda *a, **k: (None, None)
        )
        monkeypatch.setattr(llm_flow, "_provider_attempt_extra", lambda **k: {})
        monkeypatch.setattr(llm_flow, "_complete_provider_attempt", _fake_complete)
        monkeypatch.setattr(
            llm_flow, "_record_provider_attempt_result", lambda **k: None
        )
        monkeypatch.setattr(llm_flow, "_extra_with_latency", lambda extra, **k: extra)
        monkeypatch.setattr(llm_flow.time, "sleep", lambda s: sleeps.append(s))

        response = llm_flow._call_with_retries(
            _Client(), "openai", "m", SimpleNamespace(), {}
        )
        return response, attempts, sleeps

    def test_retryable_failure_is_bounded_by_max_retries(self, monkeypatch) -> None:
        response, attempts, sleeps = self._call(
            monkeypatch, error_code="RATE_LIMITED", max_retries=3
        )

        assert len(attempts) == 4  # 1 try + 3 retries, never more
        assert len(sleeps) == 3
        assert all(0 <= s <= 300 / 1000 * 2**2 for s in sleeps)
        assert response.error.code == "RATE_LIMITED"

    def test_non_retryable_failure_is_not_retried(self, monkeypatch) -> None:
        response, attempts, sleeps = self._call(
            monkeypatch, error_code="AUTH_ERROR", max_retries=5
        )

        assert len(attempts) == 1
        assert sleeps == []
        assert response.error.code == "AUTH_ERROR"

    def test_backoff_grows_and_cannot_storm(self, monkeypatch) -> None:
        _, _, sleeps = self._call(monkeypatch, error_code="TIMEOUT", max_retries=4)

        assert sleeps == [0.3, 0.6, 1.2, 2.4]  # exponential, bounded by attempts
        assert sum(sleeps) <= 5.0  # geometric ceiling for 4 attempts at 300 ms


class TestEscalationAndHandoff:
    def test_child_outcome_payload_is_carried_to_the_parent(self) -> None:
        response = SimpleNamespace(
            delegation_result_summary={
                "status": "complete",
                "summary": "child finished the sub-task",
                "artifacts_produced": ["report.md"],
            }
        )

        payload = _delegation_result_summary_payload(response)

        assert payload is not None
        assert payload.status == "complete"
        assert payload.summary == "child finished the sub-task"
        assert payload.artifacts_produced == ["report.md"]

    def test_malformed_child_outcome_is_not_interpreted_as_success(self) -> None:
        response = SimpleNamespace(delegation_result_summary={"status": "ok"})

        assert _delegation_result_summary_payload(response) is None


class TestRollback:
    def test_checkpoint_resumes_mid_workflow_without_replaying_steps(self) -> None:
        from tests.checkpoint.test_checkpoint_module import (
            _DummyWorkflow,
            _ctx,
        )
        from openminion.modules.task import TaskManager

        with tempfile.TemporaryDirectory() as tmp:
            task_manager = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
            ctx = _ctx(task_manager)
            handler = _DummyWorkflow()
            handler.initialize(ctx)

            handler.completed.append("one")
            checkpoint_id = handler._save_checkpoint(ctx, cursor=1)
            handler.completed.append("two")

            # Simulate a failure mid-workflow: in-memory work is lost.
            resumed_ctx = _ctx(task_manager, state=ctx.state.model_copy(deep=True))
            resumed_ctx.state.task_backed_task_id = ctx.state.task_backed_task_id
            resumed_handler = _DummyWorkflow()
            plan = resumed_handler.resume(resumed_ctx, checkpoint_id)

            assert plan.cursor == 1
            assert resumed_handler.completed == ["one"]

    def test_missing_checkpoint_is_an_explicit_miss_not_a_silent_resume(self) -> None:
        from openminion.modules.task import TaskManager
        from openminion.modules.brain.checkpoint.manager import CheckpointManager
        from tests.checkpoint.test_checkpoint_module import _MockConsumer

        with tempfile.TemporaryDirectory() as tmp:
            manager = CheckpointManager(
                task_service=TaskManager.for_lifecycle_db(
                    db_path=Path(tmp) / "tasks.db"
                )
            )

            assert manager.load(consumer=_MockConsumer(), task_id="never-saved") is None


class TestBudgetAwareOrchestration:
    def _history(self, count: int, chars: int = 400) -> list[Message]:
        return [
            Message(channel="console", target="cli", body="h" * chars)
            for _ in range(count)
        ]

    def test_cumulative_history_is_trimmed_to_the_turn_budget(self) -> None:
        history = self._history(10)
        budget = ContextBudgetConfig(max_tokens=300, chars_per_token=4.0)

        assembled = assemble_budgeted_context(
            system_messages=[Message(channel="console", target="cli", body="s" * 100)],
            history_messages=history,
            budget=budget,
        )

        kept = sum(len(m.body) for m in assembled.messages)
        assert kept <= budget.max_tokens * budget.chars_per_token
        assert assembled.telemetry.messages_after_trim < len(history)
        assert assembled.telemetry.trimmed_count > 0

    def test_unlimited_budget_keeps_every_message(self) -> None:
        history = self._history(6)

        assembled = assemble_budgeted_context(
            system_messages=[],
            history_messages=history,
            budget=ContextBudgetConfig(max_tokens=0),
        )

        assert len(assembled.messages) == len(history)
        assert assembled.telemetry.trimmed_count == 0
