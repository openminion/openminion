from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock

from openminion.modules.brain.config import RunnerOptions
from openminion.modules.brain.execution.continuation import continuation_choice_message
from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.runner import BrainRunner
from openminion.modules.brain.schemas import (
    ActionResult,
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    BudgetCounters,
    FeasibilityReport,
    LLMProfiles,
    Plan,
    RespondDecision,
    SubIntent,
    ToolCommand,
    WorkingState,
    build_sub_intent_id,
)
from openminion.services.brain.post_execution import BrainBridgeTurnMixin


class _DummyBridge(BrainBridgeTurnMixin):
    pass


class _DummySessionAPI:
    def __init__(self, state: dict) -> None:
        self._state = dict(state)
        self.written: dict | None = None

    def get_latest_working_state(self, session_id: str) -> dict:
        del session_id
        return dict(self._state)

    def put_working_state(self, session_id: str, *, state_inline: dict) -> None:
        del session_id
        self.written = dict(state_inline)


class _DummyRunner:
    def __init__(self, state: dict) -> None:
        self.session_api = _DummySessionAPI(state)
        self.profile = SimpleNamespace(
            budgets=SimpleNamespace(
                max_ticks_per_user_turn=8,
                max_tool_calls=8,
                max_a2a_calls=0,
                max_total_llm_tokens=100000,
                max_elapsed_ms=45000,
            )
        )


def _profile() -> AgentProfile:
    return AgentProfile(
        agent_id="control-reply-agent",
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


def _budget_counters() -> BudgetCounters:
    return BudgetCounters(
        ticks=10,
        tool_calls=5,
        a2a_calls=2,
        tokens=5000,
        time_ms=30000,
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


def _feasibility_state(session_id: str) -> WorkingState:
    intent_weather = SubIntent(
        id=build_sub_intent_id("check_weather", index=1),
        description="check_weather",
    )
    intent_flight = SubIntent(
        id=build_sub_intent_id("book_flight", index=2),
        description="book_flight",
    )
    report = FeasibilityReport(
        plan_viable=False,
        recommendation="proceed_partial",
        user_message="I can get the weather, but I can't book the flight.",
        viable_intent_ids=[intent_weather.id],
        blocked_intent_ids=[intent_flight.id],
        assessments=[
            {
                "intent_id": intent_weather.id,
                "status": "covered",
                "reason": "",
                "covering_tools": ["weather"],
            },
            {
                "intent_id": intent_flight.id,
                "status": "uncovered",
                "reason": "No booking integration is available.",
            },
        ],
    )
    return WorkingState(
        session_id=session_id,
        agent_id="control-reply-agent",
        budgets_remaining=_budget_counters(),
        goal="check weather and book me a flight",
        plan=Plan(
            objective="travel",
            steps=[
                ToolCommand(
                    title="weather",
                    tool_name="weather",
                    args={"location": "Tokyo"},
                    success_criteria={"status": "success"},
                    sub_intent_ids=[intent_weather.id],
                ),
                ToolCommand(
                    title="book flight",
                    tool_name="travel.book",
                    args={"destination": "Tokyo"},
                    success_criteria={"status": "success"},
                    sub_intent_ids=[intent_flight.id],
                ),
            ],
            stop_conditions=["travel task done"],
            assumptions=[],
            risk_summary="low",
            success_criteria={"status": "success"},
            sub_intents=[intent_weather, intent_flight],
        ),
        cursor=0,
        status="waiting_user",
        decision_sub_intents=["check_weather", "book_flight"],
        decision_sub_intent_refs=[intent_weather, intent_flight],
        decision_feasibility_state={
            **report.model_dump(mode="json"),
            "awaiting_user_choice": True,
            "reviewed": True,
            "approved_subset": False,
        },
        decision_feasibility_report=report,
    )


def _continuation_state(session_id: str) -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="control-reply-agent",
        budgets_remaining=_budget_counters(),
        goal="give me more news",
        status="waiting_user",
        decision_sub_intents=["search_news"],
        decision_success_criteria={"status": "distinct action"},
        continuation_guard_command_signature="sig-news-1",
        continuation_guard_reason=(
            "Need additional distinct work instead of rerunning the same search."
        ),
        awaiting_continuation_reply=True,
    )


def test_bridge_reset_preserves_pending_feasibility_reply_state_for_followup_continue() -> (
    None
):
    bridge = _DummyBridge()
    runner = _DummyRunner(
        {
            "status": "waiting_user",
            "phase": "ACT",
            "goal": "check weather and book me a flight",
            "plan": {
                "objective": "travel",
                "steps": [
                    {"kind": "tool", "title": "weather"},
                    {"kind": "tool", "title": "book flight"},
                ],
            },
            "cursor": 0,
            "decision_sub_intents": ["check_weather", "book_flight"],
            "decision_sub_intent_refs": [
                {"id": "intent_01_check_weather", "description": "check_weather"},
                {"id": "intent_02_book_flight", "description": "book_flight"},
            ],
            "decision_success_criteria": {"status": "success"},
            "decision_feasibility_state": {
                "awaiting_user_choice": True,
                "reviewed": True,
            },
            "decision_feasibility_report": {"plan_viable": False},
        }
    )

    bridge._reset_state_for_new_input(
        runner=runner,
        session_id="s-bridge-feasibility",
        user_input="continue",
    )

    assert runner.session_api.written is not None
    written = runner.session_api.written
    assert written["goal"] == "check weather and book me a flight"
    assert written["plan"] is not None
    assert written["decision_feasibility_state"]["awaiting_user_choice"] is True


def test_bridge_reset_preserves_continuation_reply_contract_only_for_followup_controls() -> (
    None
):
    bridge = _DummyBridge()
    base_state = {
        "status": "waiting_user",
        "goal": "give me more news",
        "decision_sub_intents": ["search_news"],
        "decision_success_criteria": {"status": "distinct action"},
        "continuation_guard_command_signature": "sig-news-1",
        "continuation_guard_reason": "Need additional distinct work.",
        "awaiting_continuation_reply": True,
        "constraints": [
            "CLOSURE_CONTINUE_PROGRESS: choose a distinct action",
            "other constraint",
        ],
    }

    followup_runner = _DummyRunner(base_state)
    bridge._reset_state_for_new_input(
        runner=followup_runner,
        session_id="s-bridge-continuation",
        user_input="continue",
    )
    assert followup_runner.session_api.written is not None
    written = followup_runner.session_api.written
    assert written["goal"] == "give me more news"
    assert written["awaiting_continuation_reply"] is True
    assert written["continuation_guard_command_signature"] == "sig-news-1"
    assert written["constraints"] == [
        "CLOSURE_CONTINUE_PROGRESS: choose a distinct action"
    ]
    assert written["decision_sub_intents"] == ["search_news"]

    fresh_runner = _DummyRunner(base_state)
    bridge._reset_state_for_new_input(
        runner=fresh_runner,
        session_id="s-bridge-continuation",
        user_input="tell me a joke",
    )
    assert fresh_runner.session_api.written is not None
    fresh = fresh_runner.session_api.written
    assert fresh["awaiting_continuation_reply"] is False
    assert fresh["continuation_guard_command_signature"] is None
    assert fresh["goal"] == "tell me a joke"


def test_feasibility_continue_replays_viable_subset_without_routing_literal_reply() -> (
    None
):
    with TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        state = _feasibility_state("feasibility-continue")
        session.put_working_state(
            state.session_id, state_inline=state.model_dump(mode="json")
        )

        runner._decide = MagicMock()
        approved_commands: list[ToolCommand] = []
        runner._approve = MagicMock(
            side_effect=lambda *, state, command, logger: (
                approved_commands.append(command) or command
            )
        )
        runner._act = MagicMock(
            return_value=(
                ActionResult(
                    command_id=state.plan.steps[0].command_id,
                    status="success",
                    summary="Weather complete.",
                ),
                None,
            )
        )
        runner._advance_after_action = MagicMock(
            side_effect=lambda *, state, action_result, force_replan=False, logger=None: (
                setattr(state, "cursor", len(state.plan.steps)),
                setattr(state, "status", "done"),
            )
        )
        runner._evaluate_turn_closure = MagicMock(
            return_value=SimpleNamespace(reason="done")
        )
        runner._apply_closure_judgment = MagicMock(return_value="close")

        output = runner.step(
            session_id=state.session_id,
            user_input="continue",
            trace_id="trace-feasibility-continue",
        )

        assert output.status == "done"
        assert runner._decide.call_count == 0
        assert approved_commands
        assert approved_commands[0].title == "weather"
        assert "continue" not in str(approved_commands[0].inputs)
        assert len(output.working_state.plan.steps) == 1
        assert output.working_state.decision_sub_intents == ["check_weather"]


def test_feasibility_skip_replays_viable_subset_without_routing_literal_reply() -> None:
    with TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        state = _feasibility_state("feasibility-skip")
        session.put_working_state(
            state.session_id, state_inline=state.model_dump(mode="json")
        )

        runner._decide = MagicMock()
        runner._approve = MagicMock(
            side_effect=lambda *, state, command, logger: command
        )
        runner._act = MagicMock(
            return_value=(
                ActionResult(
                    command_id=state.plan.steps[0].command_id,
                    status="success",
                    summary="Weather complete.",
                ),
                None,
            )
        )
        runner._advance_after_action = MagicMock(
            side_effect=lambda *, state, action_result, force_replan=False, logger=None: (
                setattr(state, "cursor", len(state.plan.steps)),
                setattr(state, "status", "done"),
            )
        )
        runner._evaluate_turn_closure = MagicMock(
            return_value=SimpleNamespace(reason="done")
        )
        runner._apply_closure_judgment = MagicMock(return_value="close")

        output = runner.step(
            session_id=state.session_id,
            user_input="skip",
            trace_id="trace-feasibility-skip",
        )

        assert output.status == "done"
        assert runner._decide.call_count == 0
        assert len(output.working_state.plan.steps) == 1
        assert output.working_state.decision_sub_intents == ["check_weather"]


def test_continuation_continue_replays_prior_goal_without_routing_literal_reply() -> (
    None
):
    with TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        state = _continuation_state("continuation-continue")
        session.put_working_state(
            state.session_id, state_inline=state.model_dump(mode="json")
        )
        runner._decide = MagicMock(
            return_value=RespondDecision(
                confidence=0.9,
                reason_code="continuation_replayed",
                respond_kind="answer",
                answer="I'll continue from the prior goal.",
            )
        )

        output = runner.step(
            session_id=state.session_id,
            user_input="continue",
            trace_id="trace-continuation-continue",
        )

        assert output.status == "done"
        assert runner._decide.call_args.kwargs["user_input"] is None
        assert output.working_state.awaiting_continuation_reply is False
        assert output.working_state.continuation_guard_command_signature == "sig-news-1"


def test_continuation_retry_reassesses_original_goal_and_clears_guard() -> None:
    with TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        state = _continuation_state("continuation-retry")
        session.put_working_state(
            state.session_id, state_inline=state.model_dump(mode="json")
        )
        runner._decide = MagicMock(
            return_value=RespondDecision(
                confidence=0.9,
                reason_code="continuation_retry",
                respond_kind="answer",
                answer="Reassessing the original request.",
            )
        )

        output = runner.step(
            session_id=state.session_id,
            user_input="retry",
            trace_id="trace-continuation-retry",
        )

        assert output.status == "done"
        assert runner._decide.call_args.kwargs["user_input"] == "give me more news"
        assert output.working_state.awaiting_continuation_reply is False
        assert output.working_state.continuation_guard_command_signature is None


def test_continuation_cancel_stops_cleanly_without_redecide() -> None:
    with TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        state = _continuation_state("continuation-cancel")
        session.put_working_state(
            state.session_id, state_inline=state.model_dump(mode="json")
        )
        runner._decide = MagicMock()

        output = runner.step(
            session_id=state.session_id,
            user_input="cancel",
            trace_id="trace-continuation-cancel",
        )

        assert output.status == "done"
        assert output.message == "Understood. I won't continue that task."
        assert runner._decide.call_count == 0
        assert output.working_state.awaiting_continuation_reply is False
        assert output.working_state.continuation_guard_command_signature is None


def test_continuation_unclear_reprompts_without_skip_semantics() -> None:
    with TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        state = _continuation_state("continuation-unclear")
        session.put_working_state(
            state.session_id, state_inline=state.model_dump(mode="json")
        )
        runner._decide = MagicMock()

        output = runner.step(
            session_id=state.session_id,
            user_input="skip",
            trace_id="trace-continuation-unclear",
        )

        assert output.status == "waiting_user"
        assert output.message == continuation_choice_message(
            "Need additional distinct work instead of rerunning the same search."
        )
        assert "skip" not in output.message
        assert runner._decide.call_count == 0
