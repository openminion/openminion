from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openminion.modules.brain.execution.feasibility import (
    apply_viable_subset,
    assess_plan_feasibility,
    build_runtime_supplement,
    feasibility_choice_message,
    parse_feasibility_choice,
)
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
    RespondDecision,
    FeasibilityReport,
    LLMProfiles,
    Plan,
    SubIntent,
    SubIntentFeasibility,
    ToolCommand,
    WorkingState,
    build_sub_intent_id,
)


def _profile() -> AgentProfile:
    return AgentProfile(
        agent_id="feasibility-agent",
        role="general",
        llm_profiles=LLMProfiles(
            decide_model="decide-default",
            plan_model="plan-default",
            act_model=None,
            reflect_model="reflect-default",
            summarize_model="summarize-default",
        ),
        budgets=AgentBudgets(
            max_ticks_per_user_turn=10,
            max_tool_calls=5,
            max_a2a_calls=2,
            max_total_llm_tokens=5000,
            max_elapsed_ms=30000,
        ),
        defaults=AgentDefaults(),
    )


def _build_runner(tmp_path: Path) -> tuple[BrainRunner, LocalSessionStore]:
    session = LocalSessionStore(tmp_path / "sessions")
    runner = BrainRunner(
        profile=_profile(),
        session_api=session,
        context_api=LocalContextAdapter(session_store=session),
        llm_api=None,
        tool_api=LocalToolAdapter(),
        a2a_api=LocalA2AAdapter(),
        memory_api=LocalMemoryAdapter(tmp_path / "memory"),
        policy_api=LocalPolicyAdapter(),
        options=RunnerOptions(metactl_enabled=False),
    )
    return runner, session


def _budget_counters() -> BudgetCounters:
    return BudgetCounters(
        ticks=10,
        tool_calls=5,
        a2a_calls=2,
        tokens=5000,
        time_ms=30000,
    )


def test_build_runtime_supplement_surfaces_transient_state_only() -> None:
    findings = build_runtime_supplement(
        tool_schemas=[
            {
                "name": "weather",
                "auth_status": "missing",
                "runtime_status": "available",
                "metadata_complete": False,
            },
            {
                "name": "web.search",
                "rate_limit_state": "limited",
                "config_status": "configured",
            },
        ]
    )

    assert findings == [
        {
            "tool_name": "weather",
            "kind": "auth_status",
            "value": "missing",
            "details": {},
        },
        {
            "tool_name": "web.search",
            "kind": "rate_limit_state",
            "value": "limited",
            "details": {},
        },
    ]


def test_parse_feasibility_choice_requires_exact_control_words() -> None:
    assert parse_feasibility_choice("continue") == "continue"
    assert parse_feasibility_choice("retry") == "retry"
    assert parse_feasibility_choice("cancel") == "cancel"
    assert parse_feasibility_choice("skip") == "skip"
    assert parse_feasibility_choice("continue please") == "unclear"
    assert parse_feasibility_choice("maybe") == "unclear"


def test_feasibility_choice_message_stays_conversational() -> None:
    report = FeasibilityReport(
        plan_viable=False,
        recommendation="proceed_partial",
        assessments=[
            {
                "intent_id": "intent-1",
                "status": "uncovered",
                "reason": "No deployment tool is available.",
            }
        ],
    )

    message = feasibility_choice_message(report)

    assert "Reply 'continue'" in message
    assert "proceed_partial" not in message
    assert "uncovered" not in message


def test_pre_execution_and_post_execution_status_models_stay_separate() -> None:
    with pytest.raises(Exception):
        SubIntentFeasibility(intent_id="intent-1", status="succeeded")

    with pytest.raises(Exception):
        WorkingState(
            session_id="bad-status",
            agent_id="feasibility-agent",
            budgets_remaining=_budget_counters(),
            intent_execution_states=[
                {
                    "intent_id": "intent-1",
                    "description": "weather",
                    "status": "covered",
                }
            ],
        )


def test_apply_viable_subset_filters_plan_and_intent_state() -> None:
    intent_a = SubIntent(
        id=build_sub_intent_id("check_weather", index=1), description="check_weather"
    )
    intent_b = SubIntent(
        id=build_sub_intent_id("book_flight", index=2), description="book_flight"
    )
    state = WorkingState(
        session_id="subset-state",
        agent_id="feasibility-agent",
        budgets_remaining=_budget_counters(),
        plan=Plan(
            objective="travel",
            steps=[
                ToolCommand(
                    title="weather",
                    tool_name="weather",
                    args={"location": "Tokyo"},
                    success_criteria={"status": "success"},
                    sub_intent_ids=[intent_a.id],
                ),
                ToolCommand(
                    title="book",
                    tool_name="exec.run",
                    args={"command": "book"},
                    success_criteria={"status": "success"},
                    sub_intent_ids=[intent_b.id],
                ),
            ],
            stop_conditions=["done"],
            assumptions=[],
            risk_summary="low",
            success_criteria={"status": "success"},
            sub_intents=[intent_a, intent_b],
        ),
        decision_sub_intents=["check_weather", "book_flight"],
        decision_sub_intent_refs=[intent_a, intent_b],
    )

    report = FeasibilityReport(
        plan_viable=False,
        recommendation="proceed_partial",
        user_message="I can do the weather lookup, but not the booking step.",
        assessments=[
            {
                "intent_id": intent_a.id,
                "status": "covered",
                "reason": "",
                "covering_tools": ["weather"],
            },
            {
                "intent_id": intent_b.id,
                "status": "uncovered",
                "reason": "No booking integration is available.",
            },
        ],
    )

    assert apply_viable_subset(state, report) is True
    assert state.plan is not None
    assert len(state.plan.steps) == 1
    assert state.plan.steps[0].title == "weather"
    assert [item.id for item in state.plan.sub_intents] == [intent_a.id]
    assert [item.id for item in state.decision_sub_intent_refs] == [intent_a.id]
    assert state.decision_sub_intents == ["check_weather"]
    assert [item.intent_id for item in state.intent_execution_states] == [intent_a.id]


def test_assess_plan_feasibility_shortcuts_simple_single_tool_plan() -> None:
    with TemporaryDirectory() as tmp:
        runner, _ = _build_runner(Path(tmp))
        intent = SubIntent(
            id=build_sub_intent_id("check_time", index=1),
            description="check_time",
        )
        state = WorkingState(
            session_id="feasibility-shortcut",
            agent_id="feasibility-agent",
            budgets_remaining=_budget_counters(),
            plan=Plan(
                objective="time lookup",
                steps=[
                    ToolCommand(
                        title="time",
                        tool_name="time",
                        args={},
                        success_criteria={"status": "success"},
                        sub_intent_ids=[intent.id],
                    )
                ],
                stop_conditions=["done"],
                assumptions=[],
                risk_summary="low",
                success_criteria={"status": "success"},
                sub_intents=[intent],
            ),
            decision_sub_intents=["check_time"],
            decision_sub_intent_refs=[intent],
        )
        runner.llm_api = MagicMock()
        runner._collect_runtime_tool_schemas = MagicMock(
            return_value=[
                {
                    "name": "time",
                    "description": "Time operations",
                    "parameters": {"type": "object", "properties": {}},
                }
            ]
        )

        report = assess_plan_feasibility(
            runner,
            state=state,
            user_input="what time is it?",
            logger=MagicMock(),
        )

        assert report is not None
        assert report.recommendation == "proceed_full"
        assert report.requires_user_choice is False
        assert report.viable_intent_ids == [intent.id]
        assert report.assessments[0].covering_tools == ["time"]
        runner.llm_api.call_structured.assert_not_called()


def test_step_continue_reuses_viable_subset_without_control_text() -> None:
    with TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        intent_a = SubIntent(
            id=build_sub_intent_id("check_weather", index=1),
            description="check_weather",
        )
        intent_b = SubIntent(
            id=build_sub_intent_id("book_flight", index=2), description="book_flight"
        )
        plan = Plan(
            objective="travel",
            steps=[
                ToolCommand(
                    title="weather",
                    tool_name="weather",
                    args={"location": "Tokyo"},
                    success_criteria={"status": "success"},
                    sub_intent_ids=[intent_a.id],
                ),
                ToolCommand(
                    title="book",
                    tool_name="exec.run",
                    args={"command": "book"},
                    success_criteria={"status": "success"},
                    sub_intent_ids=[intent_b.id],
                ),
            ],
            stop_conditions=["done"],
            assumptions=[],
            risk_summary="low",
            success_criteria={"status": "success"},
            sub_intents=[intent_a, intent_b],
        )
        report = FeasibilityReport(
            plan_viable=False,
            recommendation="proceed_partial",
            user_message="I can do the weather lookup, but not the booking step.",
            assessments=[
                {
                    "intent_id": intent_a.id,
                    "status": "covered",
                    "reason": "",
                    "covering_tools": ["weather"],
                },
                {
                    "intent_id": intent_b.id,
                    "status": "uncovered",
                    "reason": "No booking integration is available.",
                },
            ],
        )
        state = WorkingState(
            session_id="feasibility-continue",
            agent_id="feasibility-agent",
            budgets_remaining=_budget_counters(),
            goal="check weather and book me a flight",
            plan=plan,
            cursor=0,
            status="waiting_user",
            decision_sub_intents=["check_weather", "book_flight"],
            decision_sub_intent_refs=[intent_a, intent_b],
            decision_feasibility_state={
                **report.model_dump(mode="json"),
                "awaiting_user_choice": True,
                "reviewed": True,
                "approved_subset": False,
            },
            decision_feasibility_report=report,
        )
        session.put_working_state(
            state.session_id, state_inline=state.model_dump(mode="json")
        )

        runner._clarify = MagicMock(return_value=False)
        runner._evaluate_meta = MagicMock(return_value=None)
        approved_commands: list[ToolCommand] = []
        runner._approve = MagicMock(
            side_effect=lambda *, state, command, logger: (
                approved_commands.append(command) or command
            )
        )
        runner._act = MagicMock(
            return_value=(
                ActionResult(
                    command_id=plan.steps[0].command_id,
                    status="success",
                    summary="Weather complete.",
                ),
                None,
            )
        )
        runner._observe = MagicMock()
        runner._reflect = MagicMock(return_value=None)
        runner._compact = MagicMock()

        def _finish(*, state, action_result, force_replan=False, logger=None):
            del action_result, force_replan, logger
            state.cursor = len(state.plan.steps) if state.plan is not None else 0
            state.status = "done"

        runner._advance_after_action = MagicMock(side_effect=_finish)
        runner._evaluate_turn_closure = MagicMock(
            return_value=SimpleNamespace(reason="done")
        )
        runner._apply_closure_judgment = MagicMock(return_value="close")

        output = runner.step(
            session_id="feasibility-continue",
            user_input="continue",
            trace_id="trace-feasibility-continue",
        )

        assert output.status == "done"
        assert approved_commands
        assert approved_commands[0].title == "weather"
        assert "user_input" not in approved_commands[0].inputs
        assert output.working_state.plan is not None
        assert len(output.working_state.plan.steps) == 1
        assert output.working_state.decision_sub_intents == ["check_weather"]


def test_step_retry_redecides_with_original_goal() -> None:
    with TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        intent = SubIntent(
            id=build_sub_intent_id("check_weather", index=1),
            description="check_weather",
        )
        report = FeasibilityReport(
            plan_viable=False,
            recommendation="retry_full",
            user_message="I need to reassess this plan.",
            assessments=[
                {
                    "intent_id": intent.id,
                    "status": "partial",
                    "reason": "Need a cleaner plan.",
                }
            ],
        )
        state = WorkingState(
            session_id="feasibility-retry",
            agent_id="feasibility-agent",
            budgets_remaining=_budget_counters(),
            goal="what's the weather in tokyo?",
            plan=Plan(
                objective="weather",
                steps=[
                    ToolCommand(
                        title="weather",
                        tool_name="weather",
                        args={"location": "Tokyo"},
                        success_criteria={"status": "success"},
                        sub_intent_ids=[intent.id],
                    )
                ],
                stop_conditions=["done"],
                assumptions=[],
                risk_summary="low",
                success_criteria={"status": "success"},
                sub_intents=[intent],
            ),
            status="waiting_user",
            decision_sub_intents=["check_weather"],
            decision_sub_intent_refs=[intent],
            decision_feasibility_state={
                **report.model_dump(mode="json"),
                "awaiting_user_choice": True,
                "reviewed": True,
                "approved_subset": False,
            },
            decision_feasibility_report=report,
        )
        session.put_working_state(
            state.session_id, state_inline=state.model_dump(mode="json")
        )

        runner._clarify = MagicMock(return_value=False)
        runner._evaluate_meta = MagicMock(return_value=None)
        runner._decide = MagicMock(
            return_value=RespondDecision(
                confidence=0.9,
                reason_code="retry_succeeded",
                respond_kind="answer",
                answer="Let's try this again from the original request.",
            )
        )

        output = runner.step(
            session_id="feasibility-retry",
            user_input="retry",
            trace_id="trace-feasibility-retry",
        )

        assert output.status == "done"
        assert (
            runner._decide.call_args.kwargs["user_input"]
            == "what's the weather in tokyo?"
        )


def test_step_cancel_clears_blocked_plan_without_replaying() -> None:
    with TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        intent = SubIntent(
            id=build_sub_intent_id("deploy_site", index=1), description="deploy_site"
        )
        report = FeasibilityReport(
            plan_viable=False,
            recommendation="abort",
            user_message="I can't deploy this because no deployment tool is available.",
            assessments=[
                {
                    "intent_id": intent.id,
                    "status": "uncovered",
                    "reason": "No deployment tool is available.",
                }
            ],
        )
        state = WorkingState(
            session_id="feasibility-cancel",
            agent_id="feasibility-agent",
            budgets_remaining=_budget_counters(),
            goal="deploy the site",
            plan=Plan(
                objective="deploy",
                steps=[
                    ToolCommand(
                        title="deploy",
                        tool_name="exec.run",
                        args={"command": "deploy"},
                        success_criteria={"status": "success"},
                        sub_intent_ids=[intent.id],
                    )
                ],
                stop_conditions=["done"],
                assumptions=[],
                risk_summary="low",
                success_criteria={"status": "success"},
                sub_intents=[intent],
            ),
            status="waiting_user",
            decision_sub_intents=["deploy_site"],
            decision_sub_intent_refs=[intent],
            decision_feasibility_state={
                **report.model_dump(mode="json"),
                "awaiting_user_choice": True,
                "reviewed": True,
                "approved_subset": False,
            },
            decision_feasibility_report=report,
        )
        session.put_working_state(
            state.session_id, state_inline=state.model_dump(mode="json")
        )

        output = runner.step(
            session_id="feasibility-cancel",
            user_input="cancel",
            trace_id="trace-feasibility-cancel",
        )

        assert output.status == "done"
        assert output.working_state.plan is None
        assert output.working_state.decision_feasibility_state == {}
