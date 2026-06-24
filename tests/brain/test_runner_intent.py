from __future__ import annotations

import os
import tempfile
import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.llm import LocalLLMAdapter
from openminion.modules.brain.adapters.recursive import LocalRLMAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.diagnostics.events import CanonicalEventLogger
from openminion.modules.brain.runner import RunnerOptions, BrainRunner
from openminion.modules.brain.schemas import (
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    BrainMode,
    BudgetCounters,
    ClarifyPolicy,
    ClarifyQuestion,
    ClarifyRequest,
    LLMProfiles,
    Plan,
    ToolCommand,
    WorkingState,
)


def _profile(*, max_ticks_per_user_turn: int = 5) -> AgentProfile:
    budgets = AgentBudgets(
        max_ticks_per_user_turn=max_ticks_per_user_turn,
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


def test_intent_detection_helpers() -> None:
    runner = BrainRunner(profile=_profile(), session_api=MagicMock())

    # residual conversational/intent heuristic helpers removed.
    assert not hasattr(runner, "_is_greeting_intent")
    assert not hasattr(runner, "_is_social_intent")
    assert not hasattr(runner, "_is_weather_intent")
    assert not hasattr(runner, "_is_resume_intent")
    assert not hasattr(runner, "_is_tool_inventory_intent")
    assert not hasattr(runner, "_is_time_sensitive_intent")
    assert not hasattr(runner, "_extract_weather_location")
    assert not hasattr(runner, "_normalize_city_name")

    with patch.object(importlib, "import_module", wraps=importlib.import_module):
        with patch("importlib.invalidate_caches"):
            try:
                importlib.import_module("openminion.modules.brain.context.intent")
            except ModuleNotFoundError:
                pass
            else:
                raise AssertionError("context.intent module should be removed")


def test_clarify_enter_and_response_emit_events() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session = LocalSessionStore(Path(tmp) / "sessions")
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            options=RunnerOptions(metactl_enabled=False),
        )
        state = WorkingState(
            session_id="s-clarify",
            agent_id="test-agent",
            budgets_remaining=BudgetCounters(
                ticks=5,
                tool_calls=3,
                a2a_calls=1,
                tokens=1000,
                time_ms=10000,
            ),
            trace_id="t-clarify",
        )
        logger = CanonicalEventLogger(
            session_api=session,
            session_id=state.session_id,
            agent_id=state.agent_id,
        )
        request = ClarifyRequest(
            session_id=state.session_id,
            trace_id=state.trace_id or "t-clarify",
            questions=[
                ClarifyQuestion(
                    type="ambiguous_input",
                    question="Which environment?",
                    is_blocking=True,
                )
            ],
            mode=state.mode,
            policy=ClarifyPolicy.ALWAYS_ASK,
            reason="test",
        )

        result = runner._enter_clarify_mode(
            state=state,
            clarify_request=request,
            logger=logger,
        )
        assert result.status == "waiting_user"

        events = session.list_events(state.session_id)
        types = [event["type"] for event in events]
        assert "brain.clarify.requested" in types

        response = runner._process_clarification_response(
            state=state,
            user_input="prod",
            logger=logger,
            clarify_request=request,
        )
        assert response.status == "active"

        events = session.list_events(state.session_id)
        types = [event["type"] for event in events]
        assert "brain.clarify.answered" in types


def test_complex_prompt_no_longer_forces_plan_mode_after_llm_decide() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session = LocalSessionStore(Path(tmp) / "sessions")
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=LocalContextAdapter(session_store=session),
            llm_api=LocalLLMAdapter(),
            options=RunnerOptions(metactl_enabled=False),
        )
        state = runner._load_or_init_state("s-plan-force")
        state.trace_id = "t-plan-force"
        state.mode = BrainMode.GUIDED
        logger = CanonicalEventLogger(
            session_api=session,
            session_id=state.session_id,
            agent_id=state.agent_id,
        )

        decision = runner._decide(
            state=state,
            user_input=(
                "First inspect the logs, then correlate failures with deployments, "
                "and finally propose a rollback and verification strategy."
            ),
            logger=logger,
        )

        assert decision.reason_code != "complex_request_plan_forced"


def test_simple_question_does_not_force_plan_mode() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session = LocalSessionStore(Path(tmp) / "sessions")
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=LocalContextAdapter(session_store=session),
            llm_api=LocalLLMAdapter(),
            options=RunnerOptions(metactl_enabled=False),
        )
        state = runner._load_or_init_state("s-simple-qa")
        state.trace_id = "t-simple-qa"
        state.mode = BrainMode.GUIDED
        logger = CanonicalEventLogger(
            session_api=session,
            session_id=state.session_id,
            agent_id=state.agent_id,
        )

        decision = runner._decide(
            state=state,
            user_input="What is the capital of France?",
            logger=logger,
        )

        assert decision.mode == "respond"


def test_command_mode_does_not_force_plan_for_complex_prompt() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session = LocalSessionStore(Path(tmp) / "sessions")
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=LocalContextAdapter(session_store=session),
            llm_api=LocalLLMAdapter(),
            options=RunnerOptions(metactl_enabled=False),
        )
        state = runner._load_or_init_state("s-command-complex")
        state.trace_id = "t-command-complex"
        state.mode = BrainMode.COMMAND
        logger = CanonicalEventLogger(
            session_api=session,
            session_id=state.session_id,
            agent_id=state.agent_id,
        )

        decision = runner._decide(
            state=state,
            user_input=(
                "First inspect the logs, then correlate failures with deployments, "
                "and finally propose a rollback and verification strategy."
            ),
            logger=logger,
        )

        assert decision.mode != "plan"
        assert decision.reason_code != "complex_request_plan_forced"


def test_clarify_runtime_mode_no_longer_applies_thresholds() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session = LocalSessionStore(Path(tmp) / "sessions")
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            options=RunnerOptions(metactl_enabled=False),
        )
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-mode-threshold",
            agent_id=runner.profile.agent_id,
        )
        state = runner._load_or_init_state("s-mode-threshold")
        state.trace_id = "t-mode-threshold"
        state.policy = ClarifyPolicy.ASK_IF_AMBIGUOUS
        runner._save_state(state)

        autonomous_state = state.model_copy(deep=True)
        autonomous_state.mode = BrainMode.AUTONOMOUS
        autonomous_state.unresolved_clarify_items = []
        asked_autonomous = runner._clarify(
            state=autonomous_state,
            user_input="Maybe we should proceed with this task.",
            logger=logger,
        )

        guided_state = state.model_copy(deep=True)
        guided_state.mode = BrainMode.GUIDED
        guided_state.unresolved_clarify_items = []
        asked_guided = runner._clarify(
            state=guided_state,
            user_input="Maybe we should proceed with this task.",
            logger=logger,
        )

        assert asked_autonomous is False
        assert asked_guided is False

        events = session.list_events("s-mode-threshold")
        event_types = [event["type"] for event in events]
        assert "brain.clarify.llm.requested" in event_types


def test_plan_force_policy_off_disables_runtime_plan_forcing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session = LocalSessionStore(Path(tmp) / "sessions")
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=LocalContextAdapter(session_store=session),
            llm_api=LocalLLMAdapter(),
            options=RunnerOptions(
                metactl_enabled=False,
                complex_request_plan_policy="off",
            ),
        )
        state = runner._load_or_init_state("s-plan-policy-off")
        state.trace_id = "t-plan-policy-off"
        state.mode = BrainMode.GUIDED
        logger = CanonicalEventLogger(
            session_api=session,
            session_id=state.session_id,
            agent_id=state.agent_id,
        )

        decision = runner._decide(
            state=state,
            user_input=(
                "First inspect the logs, then correlate failures with deployments, "
                "and finally propose a rollback and verification strategy."
            ),
            logger=logger,
        )

        assert decision.mode != "plan"
        assert decision.reason_code != "complex_request_plan_forced"


def test_plan_force_policy_aggressive_no_longer_forces_dense_action_prompt() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session = LocalSessionStore(Path(tmp) / "sessions")
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=LocalContextAdapter(session_store=session),
            llm_api=LocalLLMAdapter(),
            options=RunnerOptions(
                metactl_enabled=False,
                complex_request_plan_policy="aggressive",
            ),
        )
        state = runner._load_or_init_state("s-plan-policy-aggressive")
        state.trace_id = "t-plan-policy-aggressive"
        state.mode = BrainMode.GUIDED
        logger = CanonicalEventLogger(
            session_api=session,
            session_id=state.session_id,
            agent_id=state.agent_id,
        )

        decision = runner._decide(
            state=state,
            user_input=(
                "Please analyze compare and summarize reliability and latency tradeoffs "
                "for this architecture under a constrained budget over the next week."
            ),
            logger=logger,
        )

        assert decision.reason_code != "complex_request_plan_forced"


def test_llm_calls_max_is_derived_from_tick_budget_on_new_state() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session = LocalSessionStore(Path(tmp) / "sessions")
        runner = BrainRunner(
            profile=_profile(max_ticks_per_user_turn=24),
            session_api=session,
            options=RunnerOptions(metactl_enabled=False),
        )
        state = runner._load_or_init_state("s-llm-calls-max")
        assert state.llm_calls_max == 24


def test_autonomous_mode_uses_recursive_turn_path() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session = LocalSessionStore(Path(tmp) / "sessions")
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=LocalContextAdapter(session_store=session),
            llm_api=LocalLLMAdapter(),
            rlm_api=LocalRLMAdapter(),
            options=RunnerOptions(
                metactl_enabled=False,
            ),
        )
        state = runner._load_or_init_state("s-autonomous")
        state.mode = BrainMode.AUTONOMOUS
        runner._save_state(state)

        with patch.object(runner, "_clarify", return_value=False):
            output = runner.step(
                session_id="s-autonomous",
                user_input="Investigate and resolve this issue autonomously.",
                trace_id="t-autonomous",
            )

        assert output.status in {"done", "waiting_user"}
        assert output.message is not None
        assert "MOCK RLM output" in output.message

        events = session.list_events("s-autonomous")
        types = [event["type"] for event in events]
        assert "brain.recursive_turn.started" in types
        assert "brain.recursive_turn.completed" in types
        started_event = next(
            event for event in events if event["type"] == "brain.recursive_turn.started"
        )
        assert (
            str(started_event.get("payload", {}).get("source", "")).strip().lower()
            == "local_mock"
        )


def test_autonomous_strict_real_rlm_blocks_local_mock() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session = LocalSessionStore(Path(tmp) / "sessions")
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=LocalContextAdapter(session_store=session),
            llm_api=LocalLLMAdapter(),
            rlm_api=LocalRLMAdapter(),
            options=RunnerOptions(
                metactl_enabled=False,
            ),
        )
        state = runner._load_or_init_state("s-autonomous-strict")
        state.mode = BrainMode.AUTONOMOUS
        runner._save_state(state)

        with patch.dict(os.environ, {"OPENMINION_BRAIN_REQUIRE_REAL_RLM": "1"}):
            with patch.object(runner, "_clarify", return_value=False):
                output = runner.step(
                    session_id="s-autonomous-strict",
                    user_input="Investigate and resolve this issue autonomously.",
                    trace_id="t-autonomous-strict",
                )

        assert output.status == "waiting_user"
        assert output.message is not None
        assert "real rlm backend" in output.message.lower()
        types = [event["type"] for event in session.list_events("s-autonomous-strict")]
        assert "brain.recursive_turn.blocked" in types
        assert "brain.recursive_turn.completed" not in types


def test_autonomous_high_risk_planned_step_requires_confirmation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session = LocalSessionStore(Path(tmp) / "sessions")
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=LocalContextAdapter(session_store=session),
            llm_api=LocalLLMAdapter(),
            rlm_api=LocalRLMAdapter(),
            options=RunnerOptions(
                metactl_enabled=False,
            ),
        )
        state = runner._load_or_init_state("s-autonomous-risk")
        state.mode = BrainMode.AUTONOMOUS
        state.plan = Plan(
            objective="dangerous autonomous task",
            steps=[
                ToolCommand(
                    title="Tool call: exec.run",
                    tool_name="exec.run",
                    args={"command": "rm -rf /tmp/demo"},
                    success_criteria={"status": "success"},
                    idempotency_key="autonomous-risk-plan-step",
                    risk_level="high",
                )
            ],
            stop_conditions=[],
            assumptions=[],
            risk_summary="",
            success_criteria={},
        )
        state.cursor = 0
        runner._save_state(state)

        with patch.object(runner, "_clarify", return_value=False):
            output = runner.step(
                session_id="s-autonomous-risk",
                user_input="continue",
                trace_id="t-autonomous-risk",
            )

        assert output.status == "waiting_user"
        assert output.message is not None
        assert "high risk" in output.message.lower()
        types = [event["type"] for event in session.list_events("s-autonomous-risk")]
        assert "brain.recursive_turn.confirmation_required" in types
        assert "brain.recursive_turn.started" not in types
