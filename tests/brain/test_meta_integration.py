from __future__ import annotations

from pathlib import Path

from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.runner import RunnerOptions, BrainRunner
from openminion.modules.brain.schemas import (
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    BudgetCounters,
    LLMProfiles,
    Plan,
    ToolCommand,
    WorkingState,
)


def _profile() -> AgentProfile:
    return AgentProfile(
        agent_id="router-agent",
        role="general",
        llm_profiles=LLMProfiles(
            decide_model="decide-default",
            plan_model="plan-default",
            act_model=None,
            reflect_model="reflect-default",
            summarize_model="summarize-default",
        ),
        budgets=AgentBudgets(
            max_ticks_per_user_turn=20,
            max_tool_calls=5,
            max_a2a_calls=5,
            max_total_llm_tokens=5000,
            max_elapsed_ms=120000,
        ),
        defaults=AgentDefaults(),
    )


class AllowAllPolicy:
    def evaluate(self, *, command, working_state, session_context):
        from openminion.modules.brain.schemas import PolicyDecision

        return PolicyDecision(outcome="ALLOW", explanation="Mock allow")


class TestMetaIntegration:
    def test_runner_uses_builtin_meta_engine(self, tmp_path: Path) -> None:
        session = LocalSessionStore(tmp_path / "sessions")
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=LocalContextAdapter(session_store=session),
            tool_api=LocalToolAdapter(),
            a2a_api=LocalA2AAdapter(),
            memory_api=LocalMemoryAdapter(tmp_path / "memory"),
            policy_api=AllowAllPolicy(),
            meta_api=None,
            options=RunnerOptions(reflection_enabled=False, metactl_enabled=True),
        )

        command = ToolCommand(
            title="high risk op",
            tool_name="dangerous",
            args={"msg": "x"},
            success_criteria={"status": "success"},
            idempotency_key="idem-meta-1",
            risk_level="high",
        )
        state = WorkingState(
            session_id="s_meta",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
            plan=Plan(
                objective="do risky",
                steps=[command],
                stop_conditions=[],
                assumptions=[],
                risk_summary="",
                success_criteria={},
            ),
            cursor=0,
            status="active",
            trace_id="trace-meta",
        )
        session.put_working_state("s_meta", state_inline=state.model_dump(mode="json"))

        runner.step(session_id="s_meta")

        events = session.list_events("s_meta")
        meta_directives = [
            event for event in events if event["type"] == "meta.directive"
        ]
        assert meta_directives

        directive_event = next(
            event
            for event in meta_directives
            if event["payload"]["meta_state"] == "HIGH_ASSURANCE"
        )
        assert directive_event["payload"]["hook"] in {
            "before_plan",
            "before_act",
            "before_respond",
        }
        assert directive_event["payload"]["meta_state"] == "HIGH_ASSURANCE"
        assert directive_event["payload"]["directive"]["require_verification"]
