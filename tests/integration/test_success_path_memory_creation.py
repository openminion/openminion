from __future__ import annotations

from openminion.modules.brain.adapters.memory import MemctlAdapter
from openminion.modules.brain.execution import extract_success_memories
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.llm import LocalLLMAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.runner import RunnerOptions
from openminion.modules.brain.schemas import (
    ActionResult,
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    BudgetCounters,
    ClosureJudgment,
    LLMProfiles,
    Plan,
    StepOutputEntry,
    SuccessMemoryConfig,
    ToolCommand,
)
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


class _Logger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type: str, payload: dict, **kwargs) -> None:
        del kwargs
        self.events.append((event_type, payload))


def _profile(config: SuccessMemoryConfig) -> AgentProfile:
    return AgentProfile(
        agent_id="success-agent",
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
            max_a2a_calls=0,
            max_total_llm_tokens=5000,
            max_elapsed_ms=60_000,
        ),
        defaults=AgentDefaults(),
        success_memory=config,
    )


def test_success_path_extraction_stages_candidates_in_memory_service(tmp_path) -> None:
    config = SuccessMemoryConfig(enabled=True)
    service = MemoryService(store=SQLiteMemoryStore(tmp_path / "memory.db"))
    session_store = LocalSessionStore(tmp_path / "sessions")
    context_api = LocalContextAdapter(session_store=session_store)
    memory_api = MemctlAdapter(service)
    logger = _Logger()

    runner = type("RunnerStub", (), {})()
    runner.profile = _profile(config)
    runner.options = RunnerOptions(success_memory_config=config)
    runner.llm_api = LocalLLMAdapter()
    runner.context_api = context_api
    runner.memory_api = memory_api
    runner._debit_tokens = lambda *args, **kwargs: None
    runner._build_context = lambda *, state, purpose, budget, hints, logger: (
        context_api.build(
            session_id=state.session_id,
            agent_id=state.agent_id,
            purpose=purpose,
            budget=budget,
            hints=hints,
        )
    )

    from openminion.modules.brain.schemas import WorkingState

    state = WorkingState(
        session_id="success-session",
        agent_id="success-agent",
        goal="Plan a weather-aware Tokyo day trip",
        budgets_remaining=BudgetCounters(
            ticks=5,
            tool_calls=5,
            a2a_calls=0,
            tokens=5000,
            time_ms=60_000,
        ),
        trace_id="trace-success-1",
        step_outputs=[
            StepOutputEntry(
                step_index=0,
                command_id="cmd-1",
                summary="Fetched weather forecast",
                outputs={"forecast": "sunny"},
                artifact_refs=["artifact-1"],
            )
        ],
        plan=Plan(
            objective="Plan a weather-aware Tokyo day trip",
            steps=[
                ToolCommand(
                    title="Get weather",
                    tool_name="weather",
                    command_id="cmd-1",
                    args={"location": "Tokyo"},
                    success_criteria={"status": "success"},
                )
            ],
            stop_conditions=["done"],
            assumptions=[],
            risk_summary="low",
            success_criteria={"status": "success"},
        ),
    )

    candidate_ids = extract_success_memories(
        runner,
        state=state,
        action_result=ActionResult(
            command_id="cmd-1",
            status="success",
            summary="done",
        ),
        judgment=ClosureJudgment(
            satisfied=True,
            reason="goal_fully_satisfied",
            next_action="close",
        ),
        logger=logger,
        outcome_snapshot={
            "decision_memory_refs": ["mem-1"],
            "decision_context_pack_version": "pack-1",
            "decision_context_recorded_at": "2026-03-29T00:00:00+00:00",
        },
    )

    assert len(candidate_ids) == 2
    candidates = [service.candidate_get(candidate_id) for candidate_id in candidate_ids]
    assert {candidate.type for candidate in candidates} == {"procedure", "tool_habit"}
    assert all(
        candidate.proposed_scope == "agent:success-agent" for candidate in candidates
    )
    assert all(
        candidate.meta["source_success_path"] is True for candidate in candidates
    )
    assert all(
        candidate.meta["source_closure_reason"] == "goal_fully_satisfied"
        for candidate in candidates
    )
    assert any(
        event_type == "brain.success_memory.completed"
        for event_type, _payload in logger.events
    )
