from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.runner import RunnerOptions, BrainRunner
from openminion.modules.brain.schemas import (
    ActionResult,
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    BudgetCounters,
    LLMProfiles,
    Plan,
    ToolCommand,
    WorkingState,
)
from openminion.modules.brain.diagnostics.telemetry import emit_brain_operation
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService
from tests.brain.runner_test_support import build_seeded_act_decision


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _profile() -> AgentProfile:
    return AgentProfile(
        agent_id="brain-telemetry-agent",
        role="general",
        llm_profiles=LLMProfiles(
            decide_model="decide-default",
            plan_model="plan-default",
            act_model=None,
            reflect_model="reflect-default",
            summarize_model="summarize-default",
        ),
        budgets=AgentBudgets(
            max_ticks_per_user_turn=4,
            max_tool_calls=2,
            max_a2a_calls=1,
            max_total_llm_tokens=1000,
            max_elapsed_ms=10000,
        ),
        defaults=AgentDefaults(),
    )


class _DummyLogger:
    def emit(self, *args, **kwargs):  # noqa: ANN002,ANN003
        return None


def test_brain_runner_emits_turn_lifecycle_pack_and_tool_loop() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        telemetry = TelemetryService(db_path)
        ctl = TelemetryCtl(telemetry)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            profile = _profile()
            profile.budgets.max_tool_calls = 4
            runner = BrainRunner(
                profile=profile,
                session_api=session,
                llm_api=None,
                context_api=LocalContextAdapter(session_store=session),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                telemetryctl=ctl,
                options=RunnerOptions(metactl_enabled=False, failure_strategy="retry"),
            )

            state = runner._load_or_init_state("sess-brain")
            state.trace_id = "trace-brain"
            runner._build_context(
                state=state,
                purpose="decide",
                budget={"max_tokens": 100},
                hints={"user_input": "hello"},
                logger=_DummyLogger(),
            )

            with patch.object(
                runner,
                "_decide",
                return_value=build_seeded_act_decision(
                    command=ToolCommand(
                        title="echo",
                        tool_name="echo",
                        args={"msg": "hello"},
                        success_criteria={"status": "success"},
                    ),
                    reason_code="single_tool",
                ),
            ):
                output = runner.run(
                    session_id="sess-brain",
                    user_input='tool echo {"msg":"hello"}',
                    trace_id="trace-brain",
                )
            assert output.status in {"done", "waiting_user"}
            if output.status == "waiting_user":
                assert (
                    "could not safely determine the next step"
                    in str(output.message or "").lower()
                )

            retry_state = WorkingState(
                session_id="sess-brain",
                agent_id="brain-telemetry-agent",
                trace_id="trace-brain",
                budgets_remaining=BudgetCounters(
                    ticks=4,
                    tool_calls=2,
                    a2a_calls=1,
                    tokens=1000,
                    time_ms=10000,
                ),
                plan=Plan(
                    objective="retry a tool step",
                    steps=[
                        ToolCommand(
                            title="retry step",
                            tool_name="echo",
                            args={"msg": "hi"},
                            success_criteria={"status": "success"},
                            idempotency_key="idem-brain-retry",
                        )
                    ],
                    stop_conditions=[],
                ),
                cursor=0,
            )
            runner._advance_after_action(
                state=retry_state,
                action_result=ActionResult(
                    command_id=retry_state.plan.steps[0].command_id,
                    status="retry",
                    summary="please retry",
                ),
            )

        summary = _run(telemetry.get_module_summary("sess-brain"))
        stats = summary["openminion-brain"]
        assert stats["operation_counts"]["turn_start"] >= 1
        assert stats["operation_counts"]["llm_pack"] >= 1
        assert stats["operation_counts"]["tool_loop"] >= 1
        assert stats["operation_counts"].get("retry", 0) == 0
        assert stats["operation_counts"]["turn_finish"] >= 1
        _run(telemetry.close())
    finally:
        os.unlink(db_path)


def test_brain_helper_rejects_unknown_operation_and_absent_adapter() -> None:
    assert (
        emit_brain_operation(
            telemetryctl=None,
            session_id="sess-brain-invalid",
            turn_id="turn-1",
            operation="unknown",
        )
        is False
    )
