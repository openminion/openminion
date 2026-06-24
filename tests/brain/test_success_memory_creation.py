from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from openminion.modules.brain.config import RunnerOptions
from openminion.modules.brain.execution import extract_success_memories
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.llm import LocalLLMAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.schemas import (
    ActionResult,
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    BudgetCounters,
    ClosureJudgment,
    IntentExecutionState,
    LLMProfiles,
    Plan,
    StepOutputEntry,
    SuccessMemoryConfig,
    ToolCommand,
    WorkingState,
)


class _Logger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict, dict]] = []

    def emit(self, event_type: str, payload: dict, **kwargs) -> None:
        self.events.append((event_type, payload, kwargs))


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


def _state() -> WorkingState:
    return WorkingState(
        session_id="success-session",
        agent_id="success-agent",
        goal="Plan a weather-aware Tokyo day trip",
        working_act_profile="research",
        decision_capability_category="live_information",
        decision_reason_code="latest_news",
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


def _runner(tmp_path: Path, *, config: SuccessMemoryConfig) -> SimpleNamespace:
    session_store = LocalSessionStore(tmp_path / "sessions")
    context_api = LocalContextAdapter(session_store=session_store)
    memory_api = LocalMemoryAdapter(tmp_path / "memory")

    def _build_context(*, state, purpose, budget, hints, logger):
        del logger
        return context_api.build(
            session_id=state.session_id,
            agent_id=state.agent_id,
            purpose=purpose,
            budget=budget,
            hints=hints,
        )

    return SimpleNamespace(
        profile=_profile(config),
        options=RunnerOptions(success_memory_config=config),
        llm_api=LocalLLMAdapter(),
        context_api=context_api,
        memory_api=memory_api,
        _build_context=_build_context,
        _debit_tokens=lambda *args, **kwargs: None,
    )


def test_extract_success_memories_skips_when_closure_does_not_close(
    tmp_path: Path,
) -> None:
    runner = _runner(
        tmp_path,
        config=SuccessMemoryConfig(enabled=True),
    )
    logger = _Logger()
    state = _state()
    candidate_ids = extract_success_memories(
        runner,
        state=state,
        action_result=ActionResult(
            command_id="cmd-1",
            status="success",
            summary="done",
        ),
        judgment=ClosureJudgment(
            satisfied=False,
            reason="need_more_work",
            next_action="replan",
        ),
        logger=logger,
    )

    assert candidate_ids == []
    assert any(
        event_type == "brain.success_memory.skipped"
        and payload["reason"] == "closure_not_satisfied"
        for event_type, payload, _kwargs in logger.events
    )


def test_extract_success_memories_stages_candidates_with_events_and_meta(
    tmp_path: Path,
) -> None:
    runner = _runner(
        tmp_path,
        config=SuccessMemoryConfig(enabled=True),
    )
    logger = _Logger()
    state = _state()

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

    assert len(candidate_ids) == 3
    lines = (
        (tmp_path / "memory" / "memory.jsonl").read_text(encoding="utf-8").splitlines()
    )
    payloads = [json.loads(line) for line in lines if line.strip()]
    records = [item for item in payloads if item.get("kind") == "record"]
    candidate_payloads = [item for item in payloads if item.get("kind") == "candidate"]

    assert len(records) == 1
    assert records[0]["record_type"] == "strategy_outcome"
    assert records[0]["content"]["strategy_id"] == "research"
    assert records[0]["content"]["outcome_status"] == "success"
    assert records[0]["content"]["capability_category"] == "live_information"
    assert records[0]["content"]["intent_category"] == "latest_news"
    assert len(candidate_payloads) == 2
    assert {item["record_type"] for item in candidate_payloads} == {
        "procedure",
        "tool_habit",
    }
    assert all(item["meta"]["source_success_path"] for item in candidate_payloads)
    assert all(
        item["meta"]["source_closure_reason"] == "goal_fully_satisfied"
        for item in candidate_payloads
    )
    assert any(
        event_type == "brain.success_memory.started"
        for event_type, _payload, _kwargs in logger.events
    )
    assert any(
        event_type == "brain.success_memory.completed"
        for event_type, _payload, _kwargs in logger.events
    )


def test_extract_success_memories_honors_require_all_steps_successful(
    tmp_path: Path,
) -> None:
    config = SuccessMemoryConfig(
        enabled=True,
        require_all_steps_successful=True,
    )
    runner = _runner(tmp_path, config=config)
    logger = _Logger()
    state = _state()
    state.intent_execution_states = [
        IntentExecutionState(
            intent_id="intent_01",
            description="fetch weather",
            status="failed",
        )
    ]

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
    )

    assert candidate_ids == []
    assert any(
        event_type == "brain.success_memory.skipped"
        and payload["reason"] == "all_steps_success_required"
        for event_type, payload, _kwargs in logger.events
    )
