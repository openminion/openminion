from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from openminion.modules.brain.config import RunnerOptions
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
    LLMProfiles,
    Plan,
    PostActionJudgment,
    ToolCommand,
    WorkingState,
    IntentExecutionState,
    SubIntent,
    build_sub_intent_id,
)


def _profile() -> AgentProfile:
    budgets = AgentBudgets(
        max_ticks_per_user_turn=8,
        max_tool_calls=4,
        max_a2a_calls=2,
        max_total_llm_tokens=4000,
        max_elapsed_ms=20000,
    )
    llm_profiles = LLMProfiles(
        decide_model="decide-default",
        plan_model="plan-default",
        act_model=None,
        reflect_model="reflect-default",
        summarize_model="summarize-default",
    )
    return AgentProfile(
        agent_id="replay-agent",
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
        llm_api=None,
        tool_api=LocalToolAdapter(),
        a2a_api=LocalA2AAdapter(),
        memory_api=LocalMemoryAdapter(tmp_path / "memory"),
        policy_api=LocalPolicyAdapter(),
        options=RunnerOptions(metactl_enabled=False),
    )
    return runner, session


def _pending_command(op: str = "tab.navigate") -> ToolCommand:
    return ToolCommand(
        title=f"browser:{op}",
        tool_name="browser",
        args={"op": op, "url": "https://example.com"},
        success_criteria={"status": "success"},
        risk_level="high",
    )


def test_confirmation_replay_new_format_propagates_and_clears_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, _session = _build_runner(Path(tmp))
        cmd = _pending_command("tab.navigate")
        intent_ids = [
            build_sub_intent_id("start_browser", index=1),
            build_sub_intent_id("navigate_to_url", index=2),
        ]
        cmd.sub_intent_ids = list(intent_ids)
        state = runner._load_or_init_state("s-replay-new-format")
        state.goal = "open browser and navigate to example.com"
        state.last_user_input = "open browser and navigate to example.com"
        state.plan = Plan(
            objective="replay",
            steps=[cmd.model_copy(deep=True)],
            stop_conditions=["single command completed"],
            assumptions=[],
            risk_summary="replay",
            success_criteria={"final_url": "https://example.com"},
            sub_intents=[
                SubIntent(id=intent_ids[0], description="start_browser"),
                SubIntent(id=intent_ids[1], description="navigate_to_url"),
            ],
        )
        state.cursor = 0
        state.pending_confirmation_command = cmd.model_copy(deep=True)
        state.pending_confirmation_sub_intents = ["start_browser", "navigate_to_url"]
        state.pending_confirmation_rationale = "one replay command is enough"
        state.pending_confirmation_success_criteria = {
            "final_url": "https://example.com"
        }
        runner._save_state(state)

        with patch.object(
            runner, "_approve", side_effect=lambda **kwargs: kwargs["command"]
        ):
            with patch.object(
                runner,
                "_act",
                return_value=(
                    ActionResult(
                        command_id="cmd-replay",
                        status="success",
                        summary="replay-done",
                    ),
                    None,
                ),
            ):
                with patch(
                    "openminion.modules.brain.execution.advance.evaluate_post_action_judgment",
                    return_value=PostActionJudgment(
                        outcome="advance", reason="test_default"
                    ),
                ):
                    output = runner.step(
                        session_id="s-replay-new-format",
                        user_input="yes",
                        trace_id="t-replay-new-format",
                    )

        assert output.status == "done"
        replay_state = output.working_state
        assert replay_state.goal == "open browser and navigate to example.com"
        assert (
            replay_state.last_user_input == "open browser and navigate to example.com"
        )
        assert replay_state.pending_confirmation_command is None
        assert replay_state.pending_confirmation_sub_intents == []
        assert replay_state.pending_confirmation_sub_intent_refs == []
        assert replay_state.pending_confirmation_rationale == ""
        assert replay_state.pending_confirmation_success_criteria == {}
        assert replay_state.pending_confirmation_feasibility_state == {}
        assert replay_state.decision_sub_intents == ["start_browser", "navigate_to_url"]
        assert [item.id for item in replay_state.decision_sub_intent_refs] == intent_ids
        assert replay_state.decision_rationale == "one replay command is enough"
        assert replay_state.decision_success_criteria == {
            "final_url": "https://example.com"
        }
        assert replay_state.decision_feasibility_state == {}
        assert [
            item.intent_id for item in replay_state.intent_execution_states
        ] == intent_ids
        assert replay_state.plan is not None
        assert [item.id for item in replay_state.plan.sub_intents] == intent_ids
        assert replay_state.plan.steps[0].sub_intent_ids == intent_ids


def test_confirmation_replay_legacy_format_without_metadata_is_graceful() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, _session = _build_runner(Path(tmp))
        cmd = _pending_command("tab.navigate")
        state = runner._load_or_init_state("s-replay-legacy-format")
        state.goal = "open browser and navigate to example.com"
        state.plan = Plan(
            objective="replay",
            steps=[cmd.model_copy(deep=True)],
            stop_conditions=["single command completed"],
            assumptions=[],
            risk_summary="replay",
            success_criteria={"status": "success"},
        )
        state.cursor = 0
        state.pending_confirmation_command = cmd.model_copy(deep=True)
        runner._save_state(state)

        with patch.object(
            runner, "_approve", side_effect=lambda **kwargs: kwargs["command"]
        ):
            with patch.object(
                runner,
                "_act",
                return_value=(
                    ActionResult(
                        command_id="cmd-legacy",
                        status="success",
                        summary="legacy-done",
                    ),
                    None,
                ),
            ):
                output = runner.step(
                    session_id="s-replay-legacy-format",
                    user_input="yes",
                    trace_id="t-replay-legacy-format",
                )

        assert output.status == "waiting_user"
        assert (
            "could not safely determine the next step"
            in str(output.message or "").lower()
        )
        replay_state = output.working_state
        assert replay_state.goal == "open browser and navigate to example.com"
        assert replay_state.pending_confirmation_command is None
        assert replay_state.decision_sub_intents == []
        assert replay_state.decision_rationale == ""
        assert replay_state.decision_success_criteria == {"status": "success"}


def test_replay_with_metadata_runs_validators_without_decide() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, _session = _build_runner(Path(tmp))
        cmd = _pending_command("instance.start")
        state = runner._load_or_init_state("s-replay-validator")
        state.plan = Plan(
            objective="replay",
            steps=[cmd.model_copy(deep=True)],
            stop_conditions=["single command completed"],
            assumptions=[],
            risk_summary="replay",
            success_criteria={"status": "success"},
        )
        state.cursor = 0
        state.pending_confirmation_command = cmd.model_copy(deep=True)
        state.pending_confirmation_sub_intents = ["start_browser", "navigate_to_url"]
        runner._save_state(state)

        output = runner.step(
            session_id="s-replay-validator",
            user_input="yes",
            trace_id="t-replay-validator",
        )
        assert output.status == "waiting_user"
        assert "requires user confirmation" in str(output.message or "")


def test_replay_metadata_roundtrip_and_shape_lock() -> None:
    intent_id = build_sub_intent_id("write_file", index=1)
    state = WorkingState(
        session_id="s-shape-lock",
        agent_id="a1",
        budgets_remaining=BudgetCounters(
            ticks=5,
            tool_calls=2,
            a2a_calls=1,
            tokens=1000,
            time_ms=10000,
        ),
        pending_confirmation_sub_intents=["write_file"],
        pending_confirmation_sub_intent_refs=[
            SubIntent(id=intent_id, description="write_file")
        ],
        pending_confirmation_rationale="single step",
        pending_confirmation_success_criteria={"path": "notes.txt"},
        pending_confirmation_feasibility_state={"status": "pending"},
        decision_sub_intents=["write_file"],
        decision_sub_intent_refs=[SubIntent(id=intent_id, description="write_file")],
        decision_rationale="single step",
        decision_success_criteria={"path": "notes.txt"},
        decision_feasibility_state={"status": "ready"},
        intent_execution_states=[
            IntentExecutionState(
                intent_id=intent_id,
                description="write_file",
                status="pending",
            )
        ],
    )
    dumped = state.model_dump(mode="json")
    loaded = WorkingState.model_validate(dumped)
    assert loaded.pending_confirmation_sub_intents == ["write_file"]
    assert [item.id for item in loaded.pending_confirmation_sub_intent_refs] == [
        intent_id
    ]
    assert loaded.pending_confirmation_rationale == "single step"
    assert loaded.pending_confirmation_success_criteria == {"path": "notes.txt"}
    assert loaded.pending_confirmation_feasibility_state == {"status": "pending"}
    assert loaded.decision_sub_intents == ["write_file"]
    assert [item.id for item in loaded.decision_sub_intent_refs] == [intent_id]
    assert loaded.decision_rationale == "single step"
    assert loaded.decision_success_criteria == {"path": "notes.txt"}
    assert loaded.decision_feasibility_state == {"status": "ready"}
    assert [item.intent_id for item in loaded.intent_execution_states] == [intent_id]

    required_fields = {
        "pending_confirmation_sub_intents",
        "pending_confirmation_sub_intent_refs",
        "pending_confirmation_rationale",
        "pending_confirmation_success_criteria",
        "pending_confirmation_feasibility_state",
        "decision_sub_intents",
        "decision_sub_intent_refs",
        "decision_rationale",
        "decision_success_criteria",
        "decision_feasibility_state",
        "intent_execution_states",
    }
    assert required_fields.issubset(set(WorkingState.model_fields))


def test_feasibility_report_backfills_from_state_payload_with_control_flags() -> None:
    intent_id = build_sub_intent_id("check_weather", index=1)
    state = WorkingState(
        session_id="s-feasibility-flags",
        agent_id="a1",
        budgets_remaining=BudgetCounters(
            ticks=5,
            tool_calls=2,
            a2a_calls=1,
            tokens=1000,
            time_ms=10000,
        ),
        decision_sub_intents=["check_weather"],
        decision_sub_intent_refs=[SubIntent(id=intent_id, description="check_weather")],
        decision_feasibility_state={
            "plan_viable": False,
            "recommendation": "proceed_partial",
            "user_message": "I can do the weather lookup but not the rest.",
            "requires_user_choice": True,
            "viable_intent_ids": [intent_id],
            "blocked_intent_ids": [],
            "assessments": [
                {
                    "intent_id": intent_id,
                    "status": "covered",
                    "reason": "",
                    "covering_tools": ["weather"],
                    "blocked_by": [],
                    "alternatives": [],
                }
            ],
            "awaiting_user_choice": True,
            "approved_subset": False,
        },
    )

    assert state.decision_feasibility_report is not None
    assert state.decision_feasibility_report.recommendation == "proceed_partial"
    assert state.decision_feasibility_state["awaiting_user_choice"] is True


def test_load_or_init_state_backfills_structured_replay_fields_from_legacy_payload() -> (
    None
):
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        legacy_state = WorkingState(
            session_id="s-legacy-structured",
            agent_id="replay-agent",
            budgets_remaining=BudgetCounters(
                ticks=5,
                tool_calls=2,
                a2a_calls=1,
                tokens=1000,
                time_ms=10000,
            ),
            pending_confirmation_sub_intents=["write_file"],
            decision_sub_intents=["write_file"],
        ).model_dump(mode="json")
        legacy_state.pop("pending_confirmation_sub_intent_refs", None)
        legacy_state.pop("pending_confirmation_feasibility_state", None)
        legacy_state.pop("decision_sub_intent_refs", None)
        legacy_state.pop("decision_feasibility_state", None)
        legacy_state.pop("intent_execution_states", None)
        session.put_working_state("s-legacy-structured", state_inline=legacy_state)

        loaded = runner._load_or_init_state("s-legacy-structured")

        intent_id = build_sub_intent_id("write_file", index=1)
        assert [item.id for item in loaded.pending_confirmation_sub_intent_refs] == [
            intent_id
        ]
        assert [item.id for item in loaded.decision_sub_intent_refs] == [intent_id]
        assert [item.intent_id for item in loaded.intent_execution_states] == [
            intent_id
        ]
        persisted = session.get_latest_working_state("s-legacy-structured") or {}
        state_inline = dict(persisted.get("state_inline", {}))
        assert "pending_confirmation_sub_intent_refs" in state_inline
        assert "decision_sub_intent_refs" in state_inline
        assert "intent_execution_states" in state_inline
