"""Research-mode contract and checkpoint tests."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

import openminion.modules.brain.loop.strategies.research.handler as research_handler_module
from openminion.modules.brain.bootstrap.route_catalog import get_route_descriptor
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.loop.strategies.research import (
    ConvergenceCheck,
    RESEARCH_MODE,
    ResearchFinding,
    ResearchMode,
    ResearchPayload,
)
from openminion.modules.brain.checkpoint.contracts import (
    TaskBackedModeContract,
    TaskProgress,
)
from openminion.modules.brain.schemas import (
    ActionResult,
    BudgetCounters,
    Plan,
    WorkingState,
)
from openminion.modules.brain.schemas.agent import ModeProfileConfig
from openminion.modules.task import TaskLifecycleState, TaskManager


# Shared test infrastructure


@dataclass
class _FakeRunner:
    task_manager: TaskManager
    llm_api: Any = None
    profile: Any = field(
        default_factory=lambda: SimpleNamespace(agent_id="router-agent")
    )

    def _build_context(self, *, state, purpose, budget, hints, logger, mode_name=None):
        del state, logger, mode_name
        return {
            "purpose": purpose,
            "budget": budget,
            "user_input": hints.get("user_input", ""),
        }

    def _debit_tokens(self, state, raw, logger) -> None:
        del state, raw, logger


@dataclass
class _StructuredLLM:
    payload: dict[str, Any]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def estimate_tokens(self, *, model: str, context: dict[str, Any]) -> int:
        del model, context
        return 100

    def call_structured(
        self, *, model: str, purpose: str, context: dict[str, Any], schema
    ):
        self.calls.append(
            {
                "model": model,
                "purpose": purpose,
                "context": context,
                "schema": getattr(schema, "__name__", str(schema)),
            }
        )
        return dict(self.payload)


@dataclass
class _FakeServices:
    runner: _FakeRunner
    statuses: list[dict[str, Any]]
    plan_calls: list[str]
    # Convergence responses: popped in order; if empty, returns "".
    convergence_queue: list[str] = field(default_factory=list)

    def save_state(self, *, state: WorkingState) -> None:
        del state

    def emit_phase_status(self, *, state: WorkingState, **kwargs) -> None:
        del state
        self.statuses.append(dict(kwargs))

    def respond_with_meta(
        self,
        *,
        state: WorkingState,
        logger: Any,
        message: str,
        status: str,
        action_result: ActionResult | None = None,
        kind: str = "assistant",
    ):
        del logger, kind
        state.status = status
        if action_result is not None:
            state.last_result = action_result
        return SimpleNamespace(
            session_id=state.session_id,
            status=status,
            message=message,
            working_state=state,
            action_result=action_result,
        )

    def direct_response(self, *, user_input, decision=None):
        del user_input, decision
        if self.convergence_queue:
            return self.convergence_queue.pop(0)
        return ""

    def plan(self, *, state, user_input, logger, decision=None):
        del state, logger, decision
        text = str(user_input or "")
        self.plan_calls.append(text)
        return Plan(objective="mock plan result.", steps=[])

    def approve_command(self, *, state, command, logger):
        del state, logger
        return command

    def act_command(self, *, state, command, logger):
        del state, command, logger
        raise AssertionError("research mode should not call ctx.act_command()")

    def assess_plan_feasibility(self, *, state, user_input, logger):
        del state, user_input, logger
        return

    def evaluate_meta(self, **kwargs):
        del kwargs
        return

    def apply_meta_directive(self, **kwargs):
        del kwargs

    def meta_override_response(self, **kwargs):
        del kwargs
        return

    def meta_tool_restriction_reason(self, *, command, directive):
        del command, directive
        return

    def command_has_side_effects(self, *, command):
        del command
        return False

    def resolve_verification_mode(self, *, current, candidate):
        return candidate if candidate is not None else current

    def verify(self, *, state, command, action_result, mode, logger):
        del state, command, action_result, mode, logger
        return True

    def improve(self, *, state, report, logger):
        del state, report, logger

    def compact(self, *, state, logger, content=""):
        del state, logger, content

    def evaluate_turn_closure(self, **kwargs):
        del kwargs
        return

    def apply_closure_judgment(self, *, state, judgment):
        del state, judgment
        return "close"

    def extract_success_memories(self, **kwargs):
        del kwargs
        return []

    def create_task(
        self,
        *,
        session_id: str,
        mode_name: str,
        goal: str,
        agent_id: str | None,
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ):
        return self.runner.task_manager.create_task(
            session_id=session_id,
            mode_name=mode_name,
            goal=goal,
            agent_id=agent_id,
            metadata=metadata,
            task_id=task_id,
        )

    def get_task(self, *, task_id: str):
        return self.runner.task_manager.get_task(task_id)

    def list_open_tasks_for_session(
        self,
        *,
        session_id: str,
        mode_name: str | None = None,
        limit: int = 100,
    ):
        return self.runner.task_manager.list_open_tasks_for_session(
            session_id,
            mode_name=mode_name,
            limit=limit,
        )

    def save_checkpoint(
        self,
        *,
        task_id: str,
        checkpoint_id: str,
        state: dict[str, Any],
    ) -> None:
        self.runner.task_manager.save_checkpoint(task_id, checkpoint_id, state)

    def get_latest_checkpoint(self, *, task_id: str):
        return self.runner.task_manager.get_latest_checkpoint(task_id)

    def list_checkpoints(self, *, task_id: str):
        return self.runner.task_manager.list_checkpoints(task_id)

    def update_task_progress(self, *, task_id: str, progress: dict[str, Any]) -> None:
        self.runner.task_manager.update_progress(task_id, progress)

    def transition_task(
        self,
        *,
        task_id: str,
        to_state: TaskLifecycleState,
        failure_reason: str | None = None,
    ):
        return self.runner.task_manager.transition_task(
            task_id=task_id,
            to_state=to_state,
            failure_reason=failure_reason,
        )

    def emit_status(self, **kwargs) -> None:
        self.statuses.append(dict(kwargs))


def _state(
    *,
    session_id: str = "s-research",
    ticks: int = 20,
    goal: str = "Research the adoption of WebAssembly",
) -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="router-agent",
        goal=goal,
        budgets_remaining=BudgetCounters(
            ticks=ticks,
            tool_calls=10,
            a2a_calls=2,
            tokens=5000,
            time_ms=120000,
        ),
        trace_id=f"trace-{session_id}",
    )


def _ctx(
    task_manager: TaskManager,
    *,
    state: WorkingState | None = None,
    research_query: str = "Research the adoption of WebAssembly",
    objective: str | None = None,
    convergence_queue: list[str] | None = None,
):
    working_state = state or _state()
    services = _FakeServices(
        runner=_FakeRunner(task_manager=task_manager),
        statuses=[],
        plan_calls=[],
        convergence_queue=list(convergence_queue or []),
    )
    decision = SimpleNamespace(
        mode=RESEARCH_MODE,
        confidence=0.9,
        reason_code="research_request",
        research_query=research_query,
        research_scope="",
        objective=objective or research_query,
        sub_intents=[],
        rationale="",
        question=None,
        answer=None,
    )
    logger = SimpleNamespace(events=[], emit=lambda *args, **kwargs: None)
    return (
        ExecutionContext(
            state=working_state,
            decision=decision,
            user_input=research_query,
            logger=logger,
            options=SimpleNamespace(),
            llm_adapter=None,
            command_executor=SimpleNamespace(),
            _services=services,
        ),
        services,
    )


def _make_mode(max_iterations: int = 3) -> ResearchMode:
    mode = ResearchMode()
    mode.apply_mode_config(
        config=SimpleNamespace(
            checkpoint_interval=1,
            max_resume_count=10,
            max_research_iterations=max_iterations,
        ),
        runner=None,
        profile=None,
    )
    return mode


_CONVERGED_RESPONSE = json.dumps(
    {"converged": True, "reasoning": "Findings sufficient.", "suggested_next_query": ""}
)
_NOT_CONVERGED_RESPONSE = json.dumps(
    {
        "converged": False,
        "reasoning": "Need more depth.",
        "suggested_next_query": "dig deeper",
    }
)


# Characterization — task-backed contract invariants


def test_research_mode_name_is_stable() -> None:
    assert ResearchMode.mode_name == RESEARCH_MODE


def test_research_mode_category_is_task_backed() -> None:
    assert ResearchMode.mode_category == "task_backed"


def test_research_mode_has_resume_flag() -> None:
    assert ResearchMode.has_resume is True


def test_research_mode_is_task_backed_contract() -> None:
    assert isinstance(ResearchMode(), TaskBackedModeContract)


def test_research_mode_is_registered_in_global_registry() -> None:
    assert get_route_descriptor(RESEARCH_MODE) is None


def test_research_mode_creates_task_on_first_execute() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm)
        mode = _make_mode(max_iterations=1)

        mode.execute(ctx)

        assert ctx.state.task_backed_task_id
        record = tm.get_task(str(ctx.state.task_backed_task_id))
        assert record is not None
        assert record.metadata.get("query") == "Research the adoption of WebAssembly"


def test_research_mode_saves_checkpoint_after_each_iteration() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm)
        mode = _make_mode(max_iterations=2)

        mode.execute(ctx)

        task_id = str(ctx.state.task_backed_task_id)
        checkpoints = tm.list_checkpoints(task_id)
        assert len(checkpoints) == 2
        assert checkpoints[0] == f"{RESEARCH_MODE}-{task_id}-cursor-1"
        assert checkpoints[1] == f"{RESEARCH_MODE}-{task_id}-cursor-2"


def test_research_mode_cancel_without_findings_returns_stopped() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm)
        mode = _make_mode(max_iterations=2)

        result = mode.cancel(ctx, "Cancelled before start.")

        assert result.status == "stopped"


def test_research_mode_invalid_resume_state_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm)
        mode = _make_mode(max_iterations=2)

        # resume with no task ID in state → error dict
        result_state = mode.resume(ctx, "missing-checkpoint")
        assert "_resume_error" in result_state


def test_research_mode_progress_emits_status() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, services = _ctx(tm)
        mode = _make_mode(max_iterations=2)
        # Create a task first so report_progress can update it.
        record = tm.create_task(
            session_id="s-research",
            mode_name=RESEARCH_MODE,
            goal="test",
            agent_id="router-agent",
        )
        ctx.state.task_backed_task_id = record.task_id

        mode.report_progress(
            ctx,
            TaskProgress(
                phase="iteration_0",
                completion_pct=0.5,
                partial_results=["some finding"],
                last_checkpoint_id="ckpt-1",
                message="Completed iteration 1.",
            ),
        )

        status_events = [s for s in services.statuses if "mode_state" in s]
        assert any(s["mode_state"] == "iteration_0" for s in status_events)


def test_research_mode_emit_partial_result_emits_status() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, services = _ctx(tm)
        mode = _make_mode(max_iterations=2)

        mode.emit_partial_result(ctx, "Interesting finding about WebAssembly.")

        assert any(s.get("mode_state") == "partial_result" for s in services.statuses)


# Schema validation tests


def test_research_payload_validates_correctly() -> None:
    payload = ResearchPayload(
        research_query="What is WebAssembly?",
        research_scope="focus on browser support",
    )
    assert payload.research_query == "What is WebAssembly?"
    assert payload.research_scope == "focus on browser support"


def test_research_payload_rejects_empty_query() -> None:
    with pytest.raises(ValidationError):
        ResearchPayload(research_query="", research_scope="")


def test_research_payload_defaults_scope_to_empty() -> None:
    payload = ResearchPayload(research_query="test query")
    assert payload.research_scope == ""


def test_research_finding_round_trips_json() -> None:
    finding = ResearchFinding(
        iteration=2,
        source_tool="plan",
        source_query="Look for adoption data",
        content="WebAssembly is growing rapidly.",
        evidence_dates=["2026-05-08T12:00:00Z"],
    )
    encoded = finding.model_dump_json()
    decoded = ResearchFinding.model_validate_json(encoded)
    assert decoded == finding


def test_research_finding_defaults_evidence_dates_to_empty_list() -> None:
    finding = ResearchFinding(
        iteration=0,
        source_tool="plan",
        source_query="q1",
        content="Finding A.",
    )
    assert finding.evidence_dates == []


def test_iteration_goal_includes_typed_current_datetime_and_evidence_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        research_handler_module,
        "_iso_now_utc",
        lambda: "2026-05-08T12:34:56+00:00",
    )
    mode = _make_mode(max_iterations=3)
    findings = [
        ResearchFinding(
            iteration=0,
            source_tool="act",
            source_query="q1",
            content="Finding A.",
            evidence_dates=["2026-05-07T09:00:00Z"],
        ).model_dump(mode="python")
    ]

    prompt = mode._build_iteration_goal(
        query="Check latest Iran news",
        scope="focus on current developments",
        findings=findings,
        convergence_hint="shipping and oil effects",
        iteration=1,
    )

    assert "current_datetime=2026-05-08T12:34:56+00:00" in prompt
    assert "evidence_date=2026-05-07T09:00:00Z" in prompt
    assert "always use the current date when reasoning" not in prompt
    assert "2025 is wrong" not in prompt


def test_convergence_no_longer_builds_an_llm_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ASRR-02 retired the LLM-judge convergence path; ``_build_convergence_prompt``
    is gone from the handler. This test pins the retirement so a silent
    revert is detected at the characterization layer (paired with
    ``tests/brain/test_asrr_llm_judge_convergence_retired.py``)."""

    del monkeypatch  # no LLM clock-stamping path remains to monkeypatch
    mode = _make_mode(max_iterations=3)
    assert not hasattr(mode, "_build_convergence_prompt"), (
        "ASRR-02: ``_build_convergence_prompt`` is the LLM-judge convergence "
        "prompt builder and was retired. Reintroducing it silently reintroduces "
        "the LLM-judge convergence anti-pattern."
    )


def test_synthesis_text_passes_typed_temporal_facts_to_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        research_handler_module,
        "_iso_now_utc",
        lambda: "2026-05-08T12:34:56+00:00",
    )
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, services = _ctx(tm)
        mode = _make_mode(max_iterations=3)
        findings = [
            ResearchFinding(
                iteration=0,
                source_tool="act",
                source_query="q1",
                content="Finding A.",
                evidence_dates=["2026-05-07T09:00:00Z"],
            ).model_dump(mode="python")
        ]

        mode._build_synthesis_text(
            ctx,
            query="Check latest Iran news",
            findings=findings,
            allow_llm_synthesis=True,
        )

        assert services.plan_calls
        prompt = services.plan_calls[-1]
        assert "current_datetime=2026-05-08T12:34:56+00:00" in prompt
        assert "evidence_date=2026-05-07T09:00:00Z" in prompt
        assert "always use the current date when reasoning" not in prompt


def test_convergence_check_validates_converged() -> None:
    check = ConvergenceCheck(
        converged=True,
        reasoning="Findings are sufficient.",
        suggested_next_query="",
    )
    assert check.converged is True


def test_convergence_check_validates_not_converged() -> None:
    check = ConvergenceCheck(
        converged=False,
        reasoning="Need more data.",
        suggested_next_query="look for more sources",
    )
    assert check.converged is False
    assert check.suggested_next_query == "look for more sources"


def test_convergence_check_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ConvergenceCheck.model_validate(
            {"converged": False, "reasoning": "ok", "unknown_field": True}
        )


def test_mode_profile_config_max_research_iterations_round_trips() -> None:
    config = ModeProfileConfig(
        checkpoint_interval=2,
        max_resume_count=8,
        max_research_iterations=7,
    )
    assert config.checkpoint_interval == 2
    assert config.max_resume_count == 8
    assert config.max_research_iterations == 7
    dumped = config.model_dump()
    restored = ModeProfileConfig.model_validate(dumped)
    assert restored.checkpoint_interval == 2
    assert restored.max_resume_count == 8
    assert restored.max_research_iterations == 7


def test_mode_profile_config_rejects_out_of_range_iterations() -> None:
    with pytest.raises(ValidationError):
        ModeProfileConfig(max_research_iterations=0)
    with pytest.raises(ValidationError):
        ModeProfileConfig(max_research_iterations=21)


def test_mode_profile_config_allows_none_iterations() -> None:
    config = ModeProfileConfig(max_research_iterations=None)
    assert config.max_research_iterations is None


# Payload extraction tests


def test_query_from_new_research_query_field() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm, research_query="Compare Rust vs Go for systems programming")
        mode = _make_mode(max_iterations=1)
        assert (
            mode._query_from_context(ctx)
            == "Compare Rust vs Go for systems programming"
        )


def test_query_falls_back_to_legacy_objective_field() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm, research_query="")
        # Clear research_query, set objective
        ctx.decision.research_query = ""
        ctx.decision.objective = "Legacy objective text"
        mode = _make_mode(max_iterations=1)
        assert mode._query_from_context(ctx) == "Legacy objective text"


def test_query_falls_back_to_state_goal() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm, research_query="")
        ctx.decision.research_query = ""
        ctx.decision.objective = ""
        ctx.state.goal = "Goal from state"
        mode = _make_mode(max_iterations=1)
        assert mode._query_from_context(ctx) == "Goal from state"


def test_query_falls_back_to_user_input() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        # Build ctx with empty research_query but non-empty user_input.
        working_state = _state()
        working_state.goal = ""
        services = _FakeServices(
            runner=_FakeRunner(task_manager=tm),
            statuses=[],
            plan_calls=[],
        )
        decision = SimpleNamespace(
            mode=RESEARCH_MODE,
            confidence=0.9,
            reason_code="research_request",
            research_query="",
            research_scope="",
            objective="",
            sub_intents=[],
            rationale="",
            question=None,
            answer=None,
        )
        logger = SimpleNamespace(events=[], emit=lambda *args, **kwargs: None)
        ctx = ExecutionContext(
            state=working_state,
            decision=decision,
            user_input="fallback user input",
            logger=logger,
            options=SimpleNamespace(),
            llm_adapter=None,
            command_executor=SimpleNamespace(),
            _services=services,
        )
        mode = _make_mode(max_iterations=1)
        result = mode._query_from_context(ctx)
        assert result == "fallback user input"


def test_missing_query_returns_waiting_user() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm, research_query="")
        ctx.decision.research_query = ""
        ctx.decision.objective = ""
        ctx.state.goal = ""
        # Rebuild ctx with empty user_input
        services = ctx._services
        empty_ctx = ExecutionContext(
            state=ctx.state,
            decision=ctx.decision,
            user_input="",
            logger=ctx.logger,
            options=ctx.options,
            llm_adapter=None,
            command_executor=SimpleNamespace(),
            _services=services,
        )
        mode = _make_mode(max_iterations=1)

        result = mode.execute(empty_ctx)

        assert result.status == "waiting_user"


# Child execution — iteration building, isolation, recursion block


def test_execute_search_iteration_returns_research_finding() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm)
        mode = _make_mode(max_iterations=2)

        finding = mode._execute_search_iteration(
            ctx,
            iteration=0,
            query="Test query",
            findings_so_far=[],
            convergence_hint="",
        )

        assert isinstance(finding, ResearchFinding)
        assert finding.iteration == 0
        assert finding.content


def test_child_state_is_isolated_from_parent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm)
        mode = _make_mode(max_iterations=2)
        from openminion.modules.brain.schemas import BudgetCounters

        parent_state = ctx.state
        child_budget = BudgetCounters(
            ticks=2, tool_calls=2, a2a_calls=1, tokens=500, time_ms=10000
        )
        child_state = mode._build_child_state(
            parent_state=parent_state,
            child_budget=child_budget,
            goal="child goal",
        )

        # Child has isolated task_backed state.
        assert child_state.task_backed_task_id is None
        assert child_state.task_backed_checkpoint_id is None
        assert child_state.task_backed_resume_state == {}
        # Child does not share budget object.
        assert child_state.budgets_remaining is not parent_state.budgets_remaining
        assert child_state.budgets_remaining.ticks == 2


def test_iteration_budget_keeps_retry_floor_without_exceeding_parent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm, state=_state(ticks=2))
        ctx.state.budgets_remaining.tool_calls = 2
        mode = _make_mode(max_iterations=2)

        first_budget = mode._iteration_budget(ctx, iteration=0)

        assert first_budget.ticks == 2
        assert first_budget.tool_calls == 2

        ctx_small, _ = _ctx(tm, state=_state(ticks=1))
        ctx_small.state.budgets_remaining.tool_calls = 1
        small_budget = mode._iteration_budget(ctx_small, iteration=0)

        assert small_budget.ticks == 1
        assert small_budget.tool_calls == 1


def test_child_iteration_dispatches_general_act_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Research child iterations should dispatch straight into a bounded
    general act pass instead of re-entering top-level decide routing.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm)
        mode = _make_mode(max_iterations=2)
        ctx._services.runner.profile.default_act_profile = "research"
        recorded: dict[str, Any] = {}

        def _fake_invoke_decision_direct(*args, **kwargs):
            runner = args[0]
            decision = kwargs["decision"]
            recorded["runner_default_act_profile_during_invoke"] = getattr(
                runner.profile, "default_act_profile", None
            )
            recorded["route"] = getattr(decision, "route", None)
            recorded["act_profile"] = getattr(decision, "act_profile", None)
            return SimpleNamespace(status="done", message="child-result")

        monkeypatch.setattr(
            "openminion.modules.brain.loop.strategies.research.handler.invoke_decision_direct",
            _fake_invoke_decision_direct,
        )

        finding = mode._execute_search_iteration(
            ctx,
            iteration=0,
            query="Test",
            findings_so_far=[],
            convergence_hint="",
        )

        assert finding.content == "child-result"
        assert finding.source_tool == "act"
        assert recorded["route"] == "act"
        assert recorded["act_profile"] == "general"
        assert recorded["runner_default_act_profile_during_invoke"] is None
        assert ctx._services.runner.profile.default_act_profile == "research"


def test_child_iteration_falls_back_to_plan_when_act_result_is_not_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, services = _ctx(tm)
        mode = _make_mode(max_iterations=2)

        def _fake_invoke_decision_direct(*args, **kwargs):
            del args, kwargs
            return SimpleNamespace(
                status="waiting_user",
                message="I hit an internal decision error before I could continue safely on this turn.",
            )

        monkeypatch.setattr(
            "openminion.modules.brain.loop.strategies.research.handler.invoke_decision_direct",
            _fake_invoke_decision_direct,
        )

        finding = mode._execute_search_iteration(
            ctx,
            iteration=0,
            query="Test",
            findings_so_far=[],
            convergence_hint="",
        )

        assert finding.source_tool == "plan"
        assert finding.content == "mock plan result."
        assert services.plan_calls


def test_child_iteration_falls_back_to_plan_on_canonical_internal_failure_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, services = _ctx(tm)
        mode = _make_mode(max_iterations=2)

        def _fake_invoke_decision_direct(*args, **kwargs):
            del args, kwargs
            return SimpleNamespace(
                status="done",
                message="I hit an internal decision error before I could continue safely on this turn.",
            )

        monkeypatch.setattr(
            "openminion.modules.brain.loop.strategies.research.handler.invoke_decision_direct",
            _fake_invoke_decision_direct,
        )

        finding = mode._execute_search_iteration(
            ctx,
            iteration=0,
            query="Test",
            findings_so_far=[],
            convergence_hint="",
        )

        assert finding.source_tool == "plan"
        assert finding.content == "mock plan result."
        assert services.plan_calls


def test_child_iteration_accepts_waiting_user_result_with_meaningful_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, services = _ctx(tm)
        mode = _make_mode(max_iterations=2)

        def _fake_invoke_decision_direct(*args, **kwargs):
            del args, kwargs
            return SimpleNamespace(
                status="waiting_user",
                message="## Deep Research Iteration\nFound current 2026 Iran developments and market effects.",
                action_result=SimpleNamespace(status="success", summary="ok"),
            )

        monkeypatch.setattr(
            "openminion.modules.brain.loop.strategies.research.handler.invoke_decision_direct",
            _fake_invoke_decision_direct,
        )

        finding = mode._execute_search_iteration(
            ctx,
            iteration=0,
            query="Test",
            findings_so_far=[],
            convergence_hint="",
        )

        assert finding.source_tool == "act"
        assert "2026 Iran developments" in finding.content
        assert not services.plan_calls


def test_child_iteration_salvages_tool_backed_content_from_budget_blocked_action_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, services = _ctx(tm)
        mode = _make_mode(max_iterations=2)

        def _fake_invoke_decision_direct(*args, **kwargs):
            del args, kwargs
            return SimpleNamespace(
                status="waiting_user",
                message="[act] budget exhausted before a final answer. Continue in a new turn or narrow the scope.",
                action_result=ActionResult(
                    command_id="budget-blocked-child",
                    status="blocked",
                    summary="[act] budget exhausted before a final answer. Continue in a new turn or narrow the scope.",
                    outputs={
                        "tool_results": [
                            {
                                "tool_name": "web.search",
                                "ok": True,
                                "content": 'Web search for "Iran latest news 2026" via serpapi returned 8 result(s).',
                            },
                            {
                                "tool_name": "web.search",
                                "ok": True,
                                "content": 'Web search for "Iran economy stock market investment outlook 2026" via serpapi returned 8 result(s).',
                            },
                        ]
                    },
                ),
            )

        monkeypatch.setattr(
            "openminion.modules.brain.loop.strategies.research.handler.invoke_decision_direct",
            _fake_invoke_decision_direct,
        )

        finding = mode._execute_search_iteration(
            ctx,
            iteration=0,
            query="Test",
            findings_so_far=[],
            convergence_hint="",
        )

        assert finding.source_tool == "act"
        assert "Iran latest news 2026" in finding.content
        assert "budget exhausted before a final answer" not in finding.content
        assert not services.plan_calls


def test_child_iteration_preserves_evidence_dates_from_tool_backed_action_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _services = _ctx(tm)
        mode = _make_mode(max_iterations=2)

        def _fake_invoke_decision_direct(*args, **kwargs):
            del args, kwargs
            return SimpleNamespace(
                status="waiting_user",
                message="Found current developments.",
                action_result=ActionResult(
                    command_id="child-with-dates",
                    status="success",
                    summary="Research child summary.",
                    outputs={
                        "tool_results": [
                            {
                                "tool_name": "web.search",
                                "ok": True,
                                "content": "Search complete.",
                                "data": {
                                    "published_at": "2026-05-08T10:00:00Z",
                                    "results": [
                                        {"date": "2026-05-07"},
                                    ],
                                },
                            }
                        ]
                    },
                ),
            )

        monkeypatch.setattr(
            "openminion.modules.brain.loop.strategies.research.handler.invoke_decision_direct",
            _fake_invoke_decision_direct,
        )

        finding = mode._execute_search_iteration(
            ctx,
            iteration=0,
            query="Test",
            findings_so_far=[],
            convergence_hint="",
        )

        assert finding.evidence_dates == ["2026-05-08T10:00:00Z"]


def test_child_iteration_ignores_failed_tool_results_when_salvaging_partials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, services = _ctx(tm)
        mode = _make_mode(max_iterations=2)

        def _fake_invoke_decision_direct(*args, **kwargs):
            del args, kwargs
            return SimpleNamespace(
                status="waiting_user",
                message="[act] budget exhausted before a final answer. Continue in a new turn or narrow the scope.",
                action_result=ActionResult(
                    command_id="budget-blocked-child",
                    status="blocked",
                    summary="[act] budget exhausted before a final answer. Continue in a new turn or narrow the scope.",
                    outputs={
                        "tool_results": [
                            {
                                "tool_name": "browser",
                                "ok": False,
                                "content": "'auto'",
                            }
                        ]
                    },
                ),
            )

        monkeypatch.setattr(
            "openminion.modules.brain.loop.strategies.research.handler.invoke_decision_direct",
            _fake_invoke_decision_direct,
        )

        finding = mode._execute_search_iteration(
            ctx,
            iteration=0,
            query="Test",
            findings_so_far=[],
            convergence_hint="",
        )

        assert finding.source_tool == "plan"
        assert finding.content == "mock plan result."
        assert services.plan_calls


def test_child_iteration_salvages_tool_backed_content_from_working_state_scratchpad(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, services = _ctx(tm)
        mode = _make_mode(max_iterations=2)

        def _fake_invoke_decision_direct(*args, **kwargs):
            del args, kwargs
            child_state = SimpleNamespace(
                scratchpad={
                    "adaptive.tool_results": [
                        {
                            "tool_name": "web.search",
                            "ok": True,
                            "content": 'Web search for "Iran sanctions latest 2026" via serpapi returned 8 result(s).',
                        }
                    ]
                }
            )
            return SimpleNamespace(
                status="waiting_user",
                message="[act] budget exhausted before a final answer. Continue in a new turn or narrow the scope.",
                action_result=ActionResult(
                    command_id="budget-blocked-child",
                    status="blocked",
                    summary="[act] budget exhausted before a final answer. Continue in a new turn or narrow the scope.",
                    outputs={},
                ),
                working_state=child_state,
            )

        monkeypatch.setattr(
            "openminion.modules.brain.loop.strategies.research.handler.invoke_decision_direct",
            _fake_invoke_decision_direct,
        )

        finding = mode._execute_search_iteration(
            ctx,
            iteration=0,
            query="Test",
            findings_so_far=[],
            convergence_hint="",
        )

        assert finding.source_tool == "act"
        assert "Iran sanctions latest 2026" in finding.content
        assert not services.plan_calls


# Convergence check and synthesis


def test_check_convergence_returns_structural_signal_under_threshold() -> None:
    """ASRR-02: with no findings (empty list), the structural convergence
    composer cannot satisfy the typed-finding-count axis (default min=3),
    so ``converged`` is False. No LLM call is made."""

    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, services = _ctx(tm)
        mode = _make_mode(max_iterations=3)

        result = mode._check_convergence(ctx, query="test", findings=[])

        assert result.converged is False
        # Structural surface — no prose ``reasoning`` field exists.
        assert not hasattr(result, "reasoning")
        # Zero LLM calls on the structural path.
        assert getattr(ctx.state, "llm_calls_used", 0) == 0
        # Convergence queue is irrelevant to the structural path.
        assert services.convergence_queue == []


def test_check_convergence_returns_structural_signal_above_threshold() -> None:
    """ASRR-02: with enough typed findings + source coverage AND no new
    evidence this turn, structural convergence fires. No LLM call."""

    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm)
        mode = _make_mode(max_iterations=3)
        # 3+ findings with 2+ distinct source pairs.
        findings = [
            ResearchFinding(
                iteration=0,
                source_tool="act",
                source_query="q1",
                content="Finding A.",
            ).model_dump(mode="python"),
            ResearchFinding(
                iteration=1,
                source_tool="plan",
                source_query="q2",
                content="Finding B.",
            ).model_dump(mode="python"),
            ResearchFinding(
                iteration=2,
                source_tool="plan",
                source_query="q3",
                content="Finding C.",
            ).model_dump(mode="python"),
        ]
        # Override config so the no_new_evidence axis is treated as
        # trivially satisfied; the per-turn delta would otherwise mark
        # progress as forward-moving in this synthetic test setup.
        mode._convergence_config = type(mode._convergence_config)(
            min_typed_finding_count=3,
            min_source_coverage=2,
            require_no_new_evidence=False,
        )

        result = mode._check_convergence(ctx, query="test", findings=findings)

        assert result.converged is True
        assert "typed_finding_count" in result.reason_axes
        assert "source_coverage" in result.reason_axes
        assert getattr(ctx.state, "llm_calls_used", 0) == 0


def test_check_convergence_defaults_to_not_converged_on_empty() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm)  # no convergence_queue → returns ""
        mode = _make_mode(max_iterations=3)

        result = mode._check_convergence(ctx, query="test", findings=[])

        assert result.converged is False


def test_check_convergence_defaults_to_not_converged_on_malformed_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, services = _ctx(
            tm,
            convergence_queue=["not-valid-json{{{"],
        )
        mode = _make_mode(max_iterations=3)

        result = mode._check_convergence(ctx, query="test", findings=[])

        assert result.converged is False


def test_synthesize_and_finalize_uses_accumulated_findings() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, services = _ctx(tm)
        mode = _make_mode(max_iterations=3)

        record = tm.create_task(
            session_id="s-research",
            mode_name=RESEARCH_MODE,
            goal="test synthesis",
            agent_id="router-agent",
        )
        ctx.state.task_backed_task_id = record.task_id
        findings = [
            ResearchFinding(
                iteration=0, source_tool="plan", source_query="q1", content="Finding A."
            ).model_dump(mode="python"),
            ResearchFinding(
                iteration=1, source_tool="plan", source_query="q2", content="Finding B."
            ).model_dump(mode="python"),
        ]

        result = mode._synthesize_and_finalize(
            ctx,
            task_id=record.task_id,
            query="What is WebAssembly?",
            findings=findings,
        )

        assert result.status == "done"
        assert ctx.state.task_backed_resume_state == {}
        # Plan call was made (synthesis prompt)
        assert services.plan_calls


# Full execute loop


def test_execute_runs_to_convergence_before_cap() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, services = _ctx(tm)
        mode = _make_mode(max_iterations=5)
        # Loosen thresholds so structural convergence fires after the
        # second iteration's typed-finding accumulation.
        mode._convergence_config = type(mode._convergence_config)(
            min_typed_finding_count=2,
            min_source_coverage=1,
            require_no_new_evidence=False,
        )

        result = mode.execute(ctx)

        assert result.status == "done"
        task_id = str(ctx.state.task_backed_task_id)
        checkpoints = tm.list_checkpoints(task_id)
        # Two iteration checkpoints saved.
        assert len(checkpoints) == 2
        # No LLM-judge consumption.
        assert services.convergence_queue == []


def test_execute_runs_to_cap_when_never_converged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm)  # convergence always returns ""  → converged=False
        mode = _make_mode(max_iterations=3)

        result = mode.execute(ctx)

        assert result.status == "done"
        task_id = str(ctx.state.task_backed_task_id)
        checkpoints = tm.list_checkpoints(task_id)
        assert len(checkpoints) == 3


def test_execute_checkpoints_after_each_iteration() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm)
        mode = _make_mode(max_iterations=2)

        mode.execute(ctx)

        task_id = str(ctx.state.task_backed_task_id)
        checkpoints = tm.list_checkpoints(task_id)
        assert checkpoints == [
            f"{RESEARCH_MODE}-{task_id}-cursor-1",
            f"{RESEARCH_MODE}-{task_id}-cursor-2",
        ]


def test_execute_pauses_when_budget_exhausted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        # ticks=1 forces pause after first iteration.
        ctx, _ = _ctx(tm, state=_state(ticks=1))
        mode = _make_mode(max_iterations=3)

        result = mode.execute(ctx)

        assert result.status == "waiting_user"
        assert "Research paused after iteration 1." in str(result.message or "")
        assert (
            "Research complete for 'Research the adoption of WebAssembly'"
            not in str(result.message or "")
        )
        task_id = str(ctx.state.task_backed_task_id)
        record = tm.get_task(task_id)
        assert record is not None
        assert record.state == TaskLifecycleState.PAUSED


def test_build_pause_response_message_preserves_partial_findings() -> None:
    mode = _make_mode(max_iterations=3)
    findings = [
        ResearchFinding(
            iteration=0,
            source_tool="plan",
            source_query="q1",
            content="Finding A.",
        ).model_dump(mode="python")
    ]

    message = mode._build_pause_response_message(
        query="What is WebAssembly?",
        findings=findings,
        iteration=0,
    )

    assert "Finding A." in message
    assert "Research complete for 'What is WebAssembly?'" not in message
    assert "Research paused after iteration 1." in message


def test_build_pause_response_message_filters_runtime_placeholder_findings() -> None:
    mode = _make_mode(max_iterations=3)
    findings = [
        ResearchFinding(
            iteration=0,
            source_tool="plan",
            source_query="q1",
            content="Research iteration 1 for 'What is WebAssembly?'.",
        ).model_dump(mode="python"),
        ResearchFinding(
            iteration=1,
            source_tool="act",
            source_query="q2",
            content="Finding B.",
        ).model_dump(mode="python"),
    ]

    message = mode._build_pause_response_message(
        query="What is WebAssembly?",
        findings=findings,
        iteration=1,
    )

    assert "Research iteration 1 for 'What is WebAssembly?'." not in message
    assert "Finding B." in message
    assert "Research complete for 'What is WebAssembly?'" not in message


def test_build_pause_response_message_omits_budget_only_placeholder_synthesis() -> None:
    mode = _make_mode(max_iterations=3)
    findings = [
        ResearchFinding(
            iteration=0,
            source_tool="act",
            source_query="q1",
            content="[act] budget exhausted before a final answer. Continue in a new turn or narrow the scope.",
        ).model_dump(mode="python")
    ]

    message = mode._build_pause_response_message(
        query="What is WebAssembly?",
        findings=findings,
        iteration=0,
    )

    assert "Research complete for 'What is WebAssembly?'" not in message
    assert "usable partial answer" in message


def test_execute_records_findings_in_checkpoint_state() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm)
        mode = _make_mode(max_iterations=2)

        mode.execute(ctx)

        task_id = str(ctx.state.task_backed_task_id)
        latest = tm.get_latest_checkpoint(task_id)
        assert latest is not None
        ckpt_state = latest[1]
        assert ckpt_state["owner"] == RESEARCH_MODE
        assert len(ckpt_state["payload"]["findings"]) == 2
        assert ckpt_state["payload"]["next_iteration"] == 2


# Resume / migration


def test_resume_continues_from_canonical_checkpoint() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm, state=_state(ticks=1))
        mode = _make_mode(max_iterations=3)

        paused = mode.execute(ctx)

        assert paused.status == "waiting_user"
        checkpoint_id = str(ctx.state.task_backed_checkpoint_id or "")
        assert checkpoint_id.endswith("-cursor-1")

        resumed_state = ctx.state.model_copy(deep=True)
        resumed_state.budgets_remaining.ticks = 20
        resumed_ctx, resumed_services = _ctx(tm, state=resumed_state)
        resumed_ctx.state.task_backed_task_id = ctx.state.task_backed_task_id
        resumed_ctx.state.task_backed_checkpoint_id = (
            ctx.state.task_backed_checkpoint_id
        )
        resumed_ctx.state.task_backed_resume_state = mode.resume(
            resumed_ctx,
            checkpoint_id,
        )

        resumed_result = mode.execute(resumed_ctx)

        assert resumed_result.status == "done"
        loaded = tm.get_task(str(ctx.state.task_backed_task_id))
        assert loaded is not None
        assert loaded.state == TaskLifecycleState.DONE
        assert int(loaded.metadata["progress"]["resume_count"]) == 1


def test_resume_migrates_legacy_checkpoint_to_findings_format() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        record = tm.create_task(
            session_id="s-migrate",
            mode_name=RESEARCH_MODE,
            goal="Legacy research task",
            agent_id="router-agent",
        )
        legacy_checkpoint_id = f"{RESEARCH_MODE}-{record.task_id}-phase-2"
        tm.save_checkpoint(
            record.task_id,
            legacy_checkpoint_id,
            {
                "task_id": record.task_id,
                "objective": "Legacy research task",
                "next_phase_index": 2,
                "partial_results": ["phase-1-result", "phase-2-result"],
                "phase_outputs": {
                    "gather_sources": "Found candidate sources.",
                    "read_sources": "Extracted key details.",
                },
                "resume_count": 0,
            },
        )

        ctx, _ = _ctx(tm, state=_state(session_id="s-migrate"))
        ctx.state.task_backed_task_id = record.task_id
        ctx.state.task_backed_checkpoint_id = legacy_checkpoint_id
        mode = _make_mode(max_iterations=5)

        resumed_state = mode.resume(ctx, legacy_checkpoint_id)

        # No resume error.
        assert "_resume_error" not in resumed_state
        # Migrated to findings-based shape.
        assert "findings" in resumed_state
        assert "next_iteration" in resumed_state
        assert resumed_state["next_iteration"] == 2
        assert len(resumed_state["findings"]) == 2
        assert any(
            f.get("source_query") == "gather_sources" for f in resumed_state["findings"]
        )


def test_resume_from_wrong_checkpoint_id_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm, state=_state(ticks=1))
        mode = _make_mode(max_iterations=3)

        mode.execute(ctx)  # Creates task + checkpoint.

        resumed_ctx, _ = _ctx(tm, state=ctx.state.model_copy(deep=True))
        resumed_ctx.state.task_backed_task_id = ctx.state.task_backed_task_id

        bad_result = mode.resume(resumed_ctx, "completely-wrong-checkpoint-id")

        assert "_resume_error" in bad_result


# Task-backed affordances


def test_cancel_preserves_findings_in_message() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm, state=_state(ticks=1))
        mode = _make_mode(max_iterations=3)

        mode.execute(ctx)  # Pauses after 1 iteration; resume_state has findings.
        cancelled = mode.cancel(ctx, "User cancelled.")

        assert cancelled.status == "stopped"
        assert "Partial findings" in str(cancelled.message or "")


def test_cancel_transitions_task_to_cancelled() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm, state=_state(ticks=1))
        mode = _make_mode(max_iterations=3)

        mode.execute(ctx)
        mode.cancel(ctx, "Stop now.")

        record = tm.get_task(str(ctx.state.task_backed_task_id))
        assert record is not None
        assert record.state == TaskLifecycleState.CANCELLED


def test_report_progress_updates_task_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm)
        mode = _make_mode(max_iterations=2)

        record = tm.create_task(
            session_id="s-research",
            mode_name=RESEARCH_MODE,
            goal="test",
            agent_id="router-agent",
        )
        ctx.state.task_backed_task_id = record.task_id
        mode.report_progress(
            ctx,
            TaskProgress(
                phase="iteration_1",
                completion_pct=0.5,
                partial_results=["finding one"],
                last_checkpoint_id="ckpt-abc",
                message="Done iteration 2.",
            ),
        )

        loaded = tm.get_task(record.task_id)
        assert loaded is not None
        assert loaded.metadata["progress"]["phase"] == "iteration_1"


def test_pause_schedules_resume_when_budget_exhausted() -> None:
    """Pause path: status is waiting_user, task transitions to paused."""
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm, state=_state(ticks=1))
        mode = _make_mode(max_iterations=3)

        result = mode.execute(ctx)

        assert result.status == "waiting_user"
        task_id = str(ctx.state.task_backed_task_id)
        record = tm.get_task(task_id)
        assert record is not None
        assert record.state == TaskLifecycleState.PAUSED
        # Checkpoint was saved for the iteration that completed.
        ckpts = tm.list_checkpoints(task_id)
        assert ckpts
        assert ckpts[-1].startswith(f"{RESEARCH_MODE}-")


def test_execute_transitions_task_to_done_on_completion() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm)
        mode = _make_mode(max_iterations=2)

        result = mode.execute(ctx)

        assert result.status == "done"
        record = tm.get_task(str(ctx.state.task_backed_task_id))
        assert record is not None
        assert record.state == TaskLifecycleState.DONE


def test_execute_progress_phase_reflects_iterations() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tm = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(tm)
        mode = _make_mode(max_iterations=2)

        mode.execute(ctx)

        record = tm.get_task(str(ctx.state.task_backed_task_id))
        assert record is not None
        progress_phase = record.metadata.get("progress", {}).get("phase", "")
        # Last reported phase is iteration_1 (zero-indexed, second iteration).
        assert progress_phase == "iteration_1"
