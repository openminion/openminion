from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch


from openminion.modules.brain.config import MissionConfig, RunnerOptions
from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.diagnostics.events import CanonicalEventLogger
from openminion.modules.brain.execution.mission import (
    allocate_mission_turn_budget,
    build_mission_state,
)
from openminion.modules.brain.runner import BrainRunner
from openminion.modules.brain.runner.lifecycle import run_until_idle
from openminion.modules.brain.schemas import (
    ActionResult,
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    BudgetCounters,
    FreshnessContract,
    FreshnessObligations,
    JobHandle,
    LLMProfiles,
    Plan,
    StepOutput,
    ToolCommand,
    WorkingState,
)
from tests.brain.runner_test_support import build_seeded_act_decision


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
        agent_id="mission-agent",
        role="general",
        llm_profiles=llm_profiles,
        budgets=budgets,
        defaults=AgentDefaults(),
    )


def _build_runner(
    tmp_path: Path,
    *,
    llm_api=None,
    context_api=None,
    mission_enabled: bool = True,
) -> tuple[BrainRunner, LocalSessionStore]:
    session = LocalSessionStore(tmp_path / "sessions")
    runner = BrainRunner(
        profile=_profile(),
        session_api=session,
        context_api=context_api,
        llm_api=llm_api,
        tool_api=LocalToolAdapter(),
        a2a_api=LocalA2AAdapter(),
        memory_api=LocalMemoryAdapter(tmp_path / "memory"),
        policy_api=LocalPolicyAdapter(),
        options=RunnerOptions(
            metactl_enabled=False,
            mission_config=MissionConfig(
                enabled=mission_enabled, max_turns_per_mission=4
            ),
        ),
    )
    return runner, session


def _echo_command(*, command_id: str = "cmd-echo") -> ToolCommand:
    return ToolCommand(
        command_id=command_id,
        title="echo",
        tool_name="echo",
        args={"msg": "hi"},
        success_criteria={"status": "success"},
    )


def _stub_successful_action_turn(
    runner: BrainRunner, *, command_id: str = "cmd-echo"
) -> None:
    runner._decide = MagicMock(  # type: ignore[method-assign]
        return_value=build_seeded_act_decision(
            confidence=1.0,
            reason_code="mission_test",
            act_profile="general",
            execution_target={"kind": "local"},
            command=_echo_command(command_id=command_id),
        )
    )
    runner._approve = MagicMock(  # type: ignore[method-assign]
        side_effect=lambda **kwargs: kwargs["command"]
    )
    runner._act = MagicMock(  # type: ignore[method-assign]
        return_value=(
            ActionResult(
                command_id=command_id,
                status="success",
                summary="ok",
            ),
            None,
        )
    )
    runner._reflect = MagicMock(return_value=None)  # type: ignore[method-assign]


class _ContextAPI:
    contract_version = "v1"

    def __init__(self) -> None:
        self.last_hints: dict[str, object] | None = None

    def build(
        self,
        *,
        session_id: str,
        agent_id: str,
        purpose: str,
        budget: dict[str, object],
        hints: dict[str, object] | None = None,
    ) -> dict[str, object]:
        del session_id, agent_id, purpose, budget
        self.last_hints = dict(hints or {})
        return {"hints": dict(hints or {})}


class _MissionJudgeLLM:
    def __init__(
        self,
        *,
        mission_payload: dict[str, object],
        closure_payload: dict[str, object] | None = None,
        repair_payload: dict[str, object] | None = None,
    ) -> None:
        self.mission_payload = dict(mission_payload)
        self.closure_payload = dict(closure_payload or {})
        self.repair_payload = dict(repair_payload or {})
        self.calls: list[str] = []

    def estimate_tokens(self, *, model: str, context: dict[str, object]) -> int:
        del model, context
        return 1

    def call_structured(
        self,
        *,
        model: str,
        purpose: str,
        context: dict[str, object],
        schema: type[object],
    ) -> dict[str, object]:
        del model, purpose, context
        self.calls.append(getattr(schema, "__name__", "unknown"))
        if getattr(schema, "__name__", "") == "ClosureJudgment":
            payload = self.closure_payload
            if not payload and "satisfied" in self.mission_payload:
                payload = self.mission_payload
            if not payload:
                payload = {
                    "satisfied": True,
                    "reason": "turn_complete",
                    "next_action": "close",
                }
            return dict(payload)
        if getattr(schema, "__name__", "") == "_ClosureFinalAnswerRepair":
            return dict(
                self.repair_payload or {"final_answer": "Completed successfully."}
            )
        return dict(self.mission_payload)


def test_interpret_reset_policy_preserves_goal_for_mission_continue() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, _session = _build_runner(Path(tmp))
        state = runner._load_or_init_state("s-interpret-mission")
        state.goal = "mission objective"
        state.mission = build_mission_state(
            runner=runner,
            state=state,
            objective="mission objective",
        )

        runner._interpret(  # type: ignore[misc]
            state=state,
            user_input="do not overwrite this objective",
            logger=MagicMock(),
            reset_policy_name="mission_continue",
        )

        assert state.goal == "mission objective"
        assert state.last_user_input == "do not overwrite this objective"
        assert state.mission.latest_reset_policy == "mission_continue"

        ordinary = runner._load_or_init_state("s-interpret-ordinary")
        ordinary.goal = "stale"
        runner._interpret(  # type: ignore[misc]
            state=ordinary,
            user_input="fresh input",
            logger=MagicMock(),
        )
        assert ordinary.goal == "fresh input"


def test_load_or_init_state_accepts_legacy_state_without_mission() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session = LocalSessionStore(Path(tmp) / "sessions")
        legacy_state = {
            "session_id": "s-legacy-mission",
            "agent_id": "mission-agent",
            "goal": "legacy goal",
            "status": "waiting_user",
            "budgets_remaining": {
                "ticks": 8,
                "tool_calls": 4,
                "a2a_calls": 2,
                "tokens": 4000,
                "time_ms": 20000,
            },
        }
        session.put_working_state("s-legacy-mission", state_inline=legacy_state)
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            options=RunnerOptions(metactl_enabled=False),
        )

        loaded = runner._load_or_init_state("s-legacy-mission")

        assert loaded.goal == "legacy goal"
        assert loaded.mission is None
        assert loaded.last_user_input == ""


def test_mission_turn_routing_preserves_objective_until_explicit_revision() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        _stub_successful_action_turn(runner)

        started = runner.step(
            session_id="s-mission-routing",
            user_input='mission: tool echo {"msg":"alpha"}',
            trace_id="trace-mission-start",
        )
        assert started.status == "waiting_user"
        assert started.working_state.mission is not None
        assert started.working_state.mission.objective == 'tool echo {"msg":"alpha"}'
        assert started.working_state.goal == 'tool echo {"msg":"alpha"}'

        continued = runner.step(
            session_id="s-mission-routing",
            user_input="continue mission",
            trace_id="trace-mission-continue",
        )
        assert continued.status == "waiting_user"
        assert continued.working_state.mission is not None
        assert continued.working_state.mission.objective == 'tool echo {"msg":"alpha"}'
        assert continued.working_state.goal == 'tool echo {"msg":"alpha"}'

        revised = runner.step(
            session_id="s-mission-routing",
            user_input='revise mission: tool echo {"msg":"beta"}',
            trace_id="trace-mission-revise",
        )
        assert revised.working_state.mission is not None
        assert revised.working_state.mission.objective == 'tool echo {"msg":"beta"}'
        assert revised.working_state.goal == 'tool echo {"msg":"beta"}'

        event_types = {
            event.get("type") for event in session.list_events("s-mission-routing")
        }
        assert "brain.mission.started" in event_types
        assert "brain.mission.continued" in event_types
        assert "brain.mission.revised" in event_types


def test_active_mission_ordinary_input_fails_closed_and_fork_pauses() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        _stub_successful_action_turn(runner)

        runner.step(
            session_id="s-mission-guard",
            user_input='mission: tool echo {"msg":"alpha"}',
            trace_id="trace-mission-guard-start",
        )
        runner._decide.reset_mock()  # type: ignore[attr-defined]

        blocked = runner.step(
            session_id="s-mission-guard",
            user_input="do something else entirely",
            trace_id="trace-mission-guard-blocked",
        )
        assert blocked.status == "waiting_user"
        assert (
            "active mission is already in progress" in (blocked.message or "").lower()
        )
        runner._decide.assert_not_called()  # type: ignore[attr-defined]

        forked = runner.step(
            session_id="s-mission-guard",
            user_input='fork: tool echo {"msg":"forked"}',
            trace_id="trace-mission-fork",
        )
        assert forked.status in {"done", "waiting_user"}
        assert forked.working_state.goal == 'tool echo {"msg":"forked"}'
        assert forked.working_state.mission is not None
        assert forked.working_state.mission.status == "paused"
        if forked.status == "waiting_user":
            assert (
                "could not safely determine the next step"
                in str(forked.message or "").lower()
            )
        event_types = {
            event.get("type") for event in session.list_events("s-mission-guard")
        }
        assert "brain.mission.paused" in event_types


def test_mission_budget_sync_is_explicit_and_ordinary_turn_unchanged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, _session = _build_runner(Path(tmp))

        state = runner._load_or_init_state("s-mission-budget")
        state.mission = build_mission_state(
            runner=runner,
            state=state,
            objective="mission budget objective",
        )
        allocate_mission_turn_budget(runner=runner, state=state)
        initial_total = state.mission.budget.total_remaining.model_copy(deep=True)
        initial_llm_total = state.mission.budget.remaining_llm_calls_total

        state.budgets_remaining.ticks -= 2
        state.budgets_remaining.tool_calls -= 1
        state.budgets_remaining.tokens -= 250
        state.llm_calls_used = 2
        runner._save_state(state)

        reloaded = runner._load_or_init_state("s-mission-budget")
        assert reloaded.mission is not None
        assert reloaded.mission.budget.total_remaining.ticks == initial_total.ticks - 2
        assert (
            reloaded.mission.budget.total_remaining.tool_calls
            == initial_total.tool_calls - 1
        )
        assert (
            reloaded.mission.budget.total_remaining.tokens == initial_total.tokens - 250
        )
        assert (
            reloaded.mission.budget.remaining_llm_calls_total == initial_llm_total - 2
        )

        ordinary = runner._load_or_init_state("s-ordinary-budget")
        ordinary_initial_ticks = ordinary.budgets_remaining.ticks
        ordinary.budgets_remaining.ticks -= 1
        runner._save_state(ordinary)
        ordinary_reloaded = runner._load_or_init_state("s-ordinary-budget")
        assert ordinary_reloaded.mission is None
        assert ordinary_reloaded.budgets_remaining.ticks == ordinary_initial_ticks - 1


def test_reconcile_pending_jobs_marks_mission_awaiting_async() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        state = runner._load_or_init_state("s-mission-pending")
        state.mission = build_mission_state(
            runner=runner,
            state=state,
            objective="async mission objective",
        )
        state.status = "job_pending"
        state.pending_jobs = [
            JobHandle(
                task_id="job-1",
                command_id="cmd-async",
                provider="tool",
                status="pending",
            )
        ]
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-mission-pending",
            agent_id=runner.profile.agent_id,
        )

        with patch(
            "openminion.modules.brain.execution.poll_async_job",
            return_value={"status": "pending"},
        ):
            output = runner._reconcile_pending_jobs(state=state, logger=logger)

        assert output is not None
        assert output.status == "job_pending"
        assert state.mission is not None
        assert state.mission.status == "awaiting_async"
        event_types = {
            event.get("type") for event in session.list_events("s-mission-pending")
        }
        assert "brain.mission.async_pending" in event_types


def test_async_resume_returns_to_active_mission_without_auto_completion() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        state = runner._load_or_init_state("s-mission-resume")
        state.mission = build_mission_state(
            runner=runner,
            state=state,
            objective="async mission objective",
        )
        state.status = "job_pending"
        command = _echo_command(command_id="cmd-async")
        state.plan = Plan(
            objective="async mission objective",
            steps=[command],
            stop_conditions=[],
            assumptions=[],
            risk_summary="",
            success_criteria={},
        )
        state.pending_jobs = [
            JobHandle(
                task_id="job-2",
                command_id="cmd-async",
                provider="tool",
                status="pending",
            )
        ]
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-mission-resume",
            agent_id=runner.profile.agent_id,
        )

        with patch(
            "openminion.modules.brain.execution.poll_async_job",
            return_value={
                "status": "success",
                "summary": "async complete",
                "outputs": {},
            },
        ):
            output = runner._reconcile_pending_jobs(state=state, logger=logger)

        assert output is not None
        assert output.status == "waiting_user"
        assert state.mission is not None
        assert state.mission.status == "active"
        assert state.mission.latest_judgment is not None
        assert state.mission.latest_judgment.outcome == "continue"
        event_types = {
            event.get("type") for event in session.list_events("s-mission-resume")
        }
        assert "brain.mission.async_resumed" in event_types
        assert "brain.mission_judge.completed" in event_types


def test_run_until_idle_pauses_mission_when_pending_handle_is_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        state = WorkingState(
            session_id="s-mission-loop",
            agent_id=runner.profile.agent_id,
            budgets_remaining=BudgetCounters(
                ticks=8,
                tool_calls=4,
                a2a_calls=2,
                tokens=4000,
                time_ms=20000,
            ),
            status="job_pending",
            trace_id="trace-mission-loop",
            pending_jobs=[],
        )
        state.mission = build_mission_state(
            runner=runner,
            state=state,
            objective="loop mission objective",
        )
        step_output = StepOutput(
            session_id="s-mission-loop",
            status="job_pending",
            working_state=state,
        )

        with patch.object(runner, "step", return_value=step_output):
            output = run_until_idle(
                runner,
                session_id="s-mission-loop",
                user_input=None,
                trace_id="trace-mission-loop",
                forced_tools=None,
                capability_category=None,
            )

        assert output.status == "waiting_user"
        assert "mission paused" in (output.message or "").lower()
        assert state.mission is not None
        assert state.mission.status == "paused"
        event_types = {
            event.get("type") for event in session.list_events("s-mission-loop")
        }
        assert "brain.mission.paused" in event_types
        assert "brain.mission.async_empty_pending" in event_types


def test_turn_closure_does_not_complete_active_mission_without_finish_request() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        context_api = _ContextAPI()
        llm_api = _MissionJudgeLLM(
            mission_payload={"outcome": "complete", "reason": "should not run"}
        )
        runner, session = _build_runner(
            Path(tmp),
            llm_api=llm_api,
            context_api=context_api,
        )
        state = runner._load_or_init_state("s-mission-close")
        state.status = "done"
        state.goal = "mission objective"
        state.mission = build_mission_state(
            runner=runner,
            state=state,
            objective="mission objective",
        )
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-mission-close",
            agent_id=runner.profile.agent_id,
        )

        judgment = runner._evaluate_turn_closure(
            state=state,
            action_result=ActionResult(
                command_id="cmd-close",
                status="success",
                summary="turn complete",
            ),
            logger=logger,
            completion_reason="plan_completed",
        )
        disposition = runner._apply_closure_judgment(state=state, judgment=judgment)

        assert disposition == "ask_user"
        assert state.status == "waiting_user"
        assert state.mission is not None
        assert state.mission.status == "active"
        assert state.mission.latest_judgment is not None
        assert state.mission.latest_judgment.outcome == "continue"
        assert "ClosureJudgment" in llm_api.calls


def test_finish_mission_requires_valid_mission_judgment_to_complete() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        context_api = _ContextAPI()
        complete_llm = _MissionJudgeLLM(
            mission_payload={
                "outcome": "complete",
                "reason": "overall mission complete",
                "final_answer": "Mission finished cleanly.",
                "confidence": 0.93,
            }
        )
        runner, session = _build_runner(
            Path(tmp),
            llm_api=complete_llm,
            context_api=context_api,
        )
        state = runner._load_or_init_state("s-mission-finish")
        state.status = "done"
        state.goal = "mission objective"
        state.mission = build_mission_state(
            runner=runner,
            state=state,
            objective="mission objective",
        )
        state.mission.latest_route_action = "finish"
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-mission-finish",
            agent_id=runner.profile.agent_id,
        )

        judgment = runner._evaluate_turn_closure(
            state=state,
            action_result=ActionResult(
                command_id="cmd-finish",
                status="success",
                summary="turn complete",
            ),
            logger=logger,
            completion_reason="plan_completed",
        )
        disposition = runner._apply_closure_judgment(state=state, judgment=judgment)

        assert disposition == "close"
        assert state.status == "done"
        assert state.mission is not None
        assert state.mission.status == "completed"
        assert judgment.final_answer == "Mission finished cleanly."
        event_types = {
            event.get("type") for event in session.list_events("s-mission-finish")
        }
        assert "brain.mission.completed" in event_types

        invalid_llm = _MissionJudgeLLM(mission_payload={"outcome": "bogus"})
        invalid_runner, invalid_session = _build_runner(
            Path(tmp) / "invalid",
            llm_api=invalid_llm,
            context_api=_ContextAPI(),
        )
        invalid_state = invalid_runner._load_or_init_state("s-mission-invalid")
        invalid_state.status = "done"
        invalid_state.goal = "mission objective"
        invalid_state.mission = build_mission_state(
            runner=invalid_runner,
            state=invalid_state,
            objective="mission objective",
        )
        invalid_state.mission.latest_route_action = "finish"
        invalid_logger = CanonicalEventLogger(
            session_api=invalid_session,
            session_id="s-mission-invalid",
            agent_id=invalid_runner.profile.agent_id,
        )

        invalid_judgment = invalid_runner._evaluate_turn_closure(
            state=invalid_state,
            action_result=ActionResult(
                command_id="cmd-invalid",
                status="success",
                summary="turn complete",
            ),
            logger=invalid_logger,
            completion_reason="plan_completed",
        )
        invalid_disposition = invalid_runner._apply_closure_judgment(
            state=invalid_state,
            judgment=invalid_judgment,
        )

        assert invalid_disposition == "ask_user"
        assert invalid_state.status == "waiting_user"
        assert invalid_state.mission is not None
        assert invalid_state.mission.status == "active"
        assert invalid_state.mission.latest_judgment is not None
        assert invalid_state.mission.latest_judgment.outcome == "ask_user"
        invalid_event_types = {
            event.get("type")
            for event in invalid_session.list_events("s-mission-invalid")
        }
        assert "brain.mission_judge.completed" in invalid_event_types


def test_turn_closure_hints_include_execution_facts_for_partial_scaffolds() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        context_api = _ContextAPI()
        llm_api = _MissionJudgeLLM(
            mission_payload={
                "satisfied": False,
                "reason": "partial scaffold only",
                "next_action": "continue",
                "final_answer": None,
            }
        )
        runner, session = _build_runner(
            Path(tmp),
            llm_api=llm_api,
            context_api=context_api,
        )
        state = runner._load_or_init_state("s-closure-facts")
        state.status = "done"
        state.goal = "Create pyproject.toml and tests/test_report.py, then run pytest until it passes."
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-closure-facts",
            agent_id=runner.profile.agent_id,
        )

        judgment = runner._evaluate_turn_closure(
            state=state,
            action_result=ActionResult(
                command_id="cmd-partial-scaffold",
                status="success",
                summary=(
                    '{"bytes_written": 6136, "mode": "write", "ok": true, '
                    '"path": "/workspace/tests/test_report.py"}'
                ),
                outputs={
                    "tool_results": [
                        {
                            "tool_name": "file.write",
                            "ok": True,
                            "verified": True,
                            "data": {
                                "path": "/workspace/tests/test_report.py",
                                "mode": "write",
                                "bytes_written": 6136,
                                "source": "file_module",
                            },
                        }
                    ],
                    "adaptive.termination_reason": "model_final",
                    "adaptive.tool_calls": ["file.write"],
                    "adaptive.tool_calls_total": 1,
                },
            ),
            logger=logger,
            completion_reason="act_seeded_commands_completed",
        )

        assert judgment.next_action == "continue"
        assert context_api.last_hints is not None
        outputs = context_api.last_hints["closure_action_outputs"]
        assert outputs["tool_execution_count"] == 1
        assert outputs["tool_name_sequence"] == ["file.write"]
        assert (
            outputs["tool_results"][0]["data"]["path"]
            == "/workspace/tests/test_report.py"
        )
        contract = context_api.last_hints["style_overrides"]["closure_gate_contract"]
        assert "closure_action_outputs" in contract
        assert "partial scaffold" in contract
        assert "required headings" in contract
        assert "progress note" in contract


def test_turn_closure_repairs_missing_final_answer_before_close() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        context_api = _ContextAPI()
        llm_api = _MissionJudgeLLM(
            mission_payload={"outcome": "complete", "reason": "unused"},
            closure_payload={
                "satisfied": True,
                "reason": "all artifacts complete",
                "next_action": "close",
                "final_answer": None,
            },
            repair_payload={
                "final_answer": "The scratch project is complete and pytest passed."
            },
        )
        runner, session = _build_runner(
            Path(tmp),
            llm_api=llm_api,
            context_api=context_api,
        )
        state = runner._load_or_init_state("s-closure-repair")
        state.status = "done"
        state.goal = "Create the scratch project and verify it passes pytest."
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-closure-repair",
            agent_id=runner.profile.agent_id,
        )

        judgment = runner._evaluate_turn_closure(
            state=state,
            action_result=ActionResult(
                command_id="cmd-repair",
                status="success",
                summary="pytest passed",
                outputs={
                    "tool_results": [
                        {
                            "tool_name": "exec.run",
                            "ok": True,
                            "data": {"argv": ["pytest"], "exit_code": 0},
                        }
                    ]
                },
            ),
            logger=logger,
            completion_reason="coding_final_text",
        )

        assert judgment.satisfied is True
        assert judgment.next_action == "close"
        assert judgment.final_answer == (
            "The scratch project is complete and pytest passed."
        )
        assert llm_api.calls == ["ClosureJudgment", "_ClosureFinalAnswerRepair"]


def test_turn_closure_rejects_mutation_claim_without_mutation_tool_evidence() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        context_api = _ContextAPI()
        llm_api = _MissionJudgeLLM(
            mission_payload={"outcome": "complete", "reason": "unused"},
            closure_payload={
                "satisfied": True,
                "reason": "claimed file updates",
                "next_action": "close",
                "final_answer": (
                    "CHANGES\n"
                    "- Modified pyproject.toml: Added `[project.scripts]`.\n"
                    "TESTS\n"
                    "- Command exited with code 0."
                ),
            },
        )
        runner, session = _build_runner(
            Path(tmp),
            llm_api=llm_api,
            context_api=context_api,
        )
        state = runner._load_or_init_state("s-mutation-claim-without-tool-evidence")
        state.status = "done"
        state.goal = "Update pyproject.toml and README.md, then run tests."
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-mutation-claim-without-tool-evidence",
            agent_id=runner.profile.agent_id,
        )

        judgment = runner._evaluate_turn_closure(
            state=state,
            action_result=ActionResult(
                command_id="cmd-mutation-claim",
                status="success",
                summary="model claimed changes without mutation tools",
                outputs={
                    "tool_results": [
                        {"tool_name": "file.read", "ok": True},
                        {"tool_name": "exec.run", "ok": True},
                    ],
                },
            ),
            logger=logger,
            completion_reason="research_final_text",
        )

        assert judgment.satisfied is False
        assert judgment.next_action == "continue"
        assert judgment.final_answer is None
        assert "mutation_claim_without_tool_evidence" in judgment.reason


def test_turn_closure_rejects_progress_note_close_answer() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        context_api = _ContextAPI()
        llm_api = _MissionJudgeLLM(
            mission_payload={"outcome": "complete", "reason": "unused"},
            closure_payload={
                "satisfied": True,
                "reason": "tool completed",
                "next_action": "close",
                "final_answer": (
                    "The pyproject.toml was created. Now I need to create the "
                    "remaining files."
                ),
            },
        )
        runner, session = _build_runner(
            Path(tmp),
            llm_api=llm_api,
            context_api=context_api,
        )
        state = runner._load_or_init_state("s-progress-note-close")
        state.status = "done"
        state.goal = "Create the scratch project and verify it passes pytest."
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-progress-note-close",
            agent_id=runner.profile.agent_id,
        )

        judgment = runner._evaluate_turn_closure(
            state=state,
            action_result=ActionResult(
                command_id="cmd-progress-note",
                status="success",
                summary="created pyproject.toml",
                outputs={
                    "tool_results": [
                        {
                            "tool_name": "file.write",
                            "ok": True,
                            "data": {
                                "path": "/workspace/pyproject.toml",
                                "mode": "write",
                                "bytes_written": 42,
                            },
                        }
                    ],
                },
            ),
            logger=logger,
            completion_reason="coding_final_text",
        )

        assert judgment.satisfied is False
        assert judgment.next_action == "continue"
        assert judgment.final_answer is None
        assert "progress_note_without_completion" in judgment.reason


def test_turn_closure_continues_when_closure_schema_is_invalid() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        context_api = _ContextAPI()
        llm_api = _MissionJudgeLLM(mission_payload={"outcome": "complete"})
        runner, session = _build_runner(
            Path(tmp),
            llm_api=llm_api,
            context_api=context_api,
        )
        state = runner._load_or_init_state("s-invalid-closure-schema")
        state.status = "done"
        state.goal = "Update files and report completion."
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-invalid-closure-schema",
            agent_id=runner.profile.agent_id,
        )

        with patch(
            "openminion.modules.brain.execution.closure.call_structured_with_retry",
            side_effect=RuntimeError("LLM did not return structured output"),
        ):
            judgment = runner._evaluate_turn_closure(
                state=state,
                action_result=ActionResult(
                    command_id="cmd-invalid-closure-schema",
                    status="success",
                    summary="workspace updated",
                ),
                logger=logger,
                completion_reason="coding_final_text",
            )

        assert judgment.satisfied is False
        assert judgment.next_action == "continue"
        assert judgment.reason == "closure_gate_invalid_structured_output"


def test_turn_closure_continues_when_freshness_evidence_missing_but_budget_remains() -> (
    None
):
    with tempfile.TemporaryDirectory() as tmp:
        context_api = _ContextAPI()
        llm_api = _MissionJudgeLLM(
            mission_payload={"outcome": "complete", "reason": "unused"},
            closure_payload={
                "satisfied": True,
                "reason": "freshness answer ready",
                "next_action": "close",
                "final_answer": "Today's package-management guidance is current.",
            },
        )
        runner, session = _build_runner(
            Path(tmp),
            llm_api=llm_api,
            context_api=context_api,
        )
        state = runner._load_or_init_state("s-freshness-continue")
        state.status = "done"
        state.goal = "Research the current package-management guidance for today."
        state.freshness_contract = FreshnessContract(
            intent="current_package_management_guidance",
            domain="general",
            time_sensitive=True,
            needs_live_data=True,
            needs_sources=True,
            needs_exact_date=True,
            answer_mode="browse_then_answer",
            reason="Current guidance requires live dated evidence.",
        )
        state.freshness_obligations = FreshnessObligations(
            require_live_data=True,
            require_sources=True,
            require_exact_date=True,
            require_explicit_failure_wording=True,
            answer_mode="browse_then_answer",
        )
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-freshness-continue",
            agent_id=runner.profile.agent_id,
        )

        judgment = runner._evaluate_turn_closure(
            state=state,
            action_result=ActionResult(
                command_id="cmd-stale",
                status="success",
                summary="answer lacks dated live evidence",
            ),
            logger=logger,
            completion_reason="act_adaptive_final_text",
        )

        assert judgment.satisfied is False
        assert judgment.next_action == "continue"
        assert judgment.final_answer is None
        assert "freshness_verifier_blocked" in judgment.reason


def test_turn_closure_refuses_close_when_final_answer_repair_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        context_api = _ContextAPI()
        llm_api = _MissionJudgeLLM(
            mission_payload={"outcome": "complete", "reason": "unused"},
            closure_payload={
                "satisfied": True,
                "reason": "all artifacts complete",
                "next_action": "close",
                "final_answer": None,
            },
            repair_payload={"final_answer": ""},
        )
        runner, session = _build_runner(
            Path(tmp),
            llm_api=llm_api,
            context_api=context_api,
        )
        state = runner._load_or_init_state("s-closure-repair-fail")
        state.status = "done"
        state.goal = "Create the scratch project and verify it passes pytest."
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-closure-repair-fail",
            agent_id=runner.profile.agent_id,
        )

        judgment = runner._evaluate_turn_closure(
            state=state,
            action_result=ActionResult(
                command_id="cmd-repair-fail",
                status="success",
                summary="pytest passed",
            ),
            logger=logger,
            completion_reason="coding_final_text",
        )

        assert judgment.satisfied is False
        assert judgment.next_action == "continue"
        assert judgment.final_answer is None
        assert "closure_missing_final_answer" in judgment.reason


def test_turn_closure_normalizes_unsatisfied_close_to_continue() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        llm_api = _MissionJudgeLLM(
            mission_payload={"outcome": "complete", "reason": "unused"},
            closure_payload={
                "satisfied": False,
                "reason": "partial scaffold only",
                "next_action": "close",
                "final_answer": "I cannot finish with the available budget.",
            },
        )
        runner, session = _build_runner(
            Path(tmp), llm_api=llm_api, context_api=_ContextAPI()
        )
        state = runner._load_or_init_state("s-closure-unsatisfied-close")
        state.status = "done"
        state.goal = "Create the scratch project and verify it passes pytest."
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-closure-unsatisfied-close",
            agent_id=runner.profile.agent_id,
        )

        judgment = runner._evaluate_turn_closure(
            state=state,
            action_result=ActionResult(
                command_id="cmd-partial",
                status="success",
                summary="only pyproject.toml was written",
            ),
            logger=logger,
            completion_reason="act_seeded_commands_completed",
        )

        assert judgment.satisfied is False
        assert judgment.next_action == "continue"
        assert judgment.final_answer is None
        assert "inconsistent_unsatisfied_close" in judgment.reason
