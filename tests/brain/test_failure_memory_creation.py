from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from openminion.modules.brain.config import RunnerOptions
from openminion.modules.brain.execution import extract_failure_memories
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.llm import LocalLLMAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.schemas import (
    ActionError,
    ActionResult,
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    BudgetCounters,
    LLMProfiles,
    Plan,
    StepOutputEntry,
    ToolCommand,
    WorkingState,
)


class _Logger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict, dict]] = []

    def emit(self, event_type: str, payload: dict, **kwargs) -> None:
        self.events.append((event_type, payload, kwargs))


def _profile() -> AgentProfile:
    return AgentProfile(
        agent_id="failure-agent",
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
    )


def _state() -> WorkingState:
    return WorkingState(
        session_id="failure-session",
        agent_id="failure-agent",
        goal="Check the weather via web.search",
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
        trace_id="trace-failure-1",
        step_outputs=[
            StepOutputEntry(
                step_index=0,
                command_id="cmd-1",
                summary="web.search failed with auth error",
                outputs={"status": "failed"},
                artifact_refs=[],
            )
        ],
        plan=Plan(
            objective="Check the weather via web.search",
            steps=[
                ToolCommand(
                    title="Run web.search",
                    tool_name="web.search",
                    command_id="cmd-1",
                    args={"query": "sf weather"},
                    success_criteria={"status": "success"},
                )
            ],
            stop_conditions=["done"],
            assumptions=[],
            risk_summary="low",
            success_criteria={"status": "success"},
        ),
    )


def _runner(tmp_path: Path) -> SimpleNamespace:
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
        profile=_profile(),
        options=RunnerOptions(metactl_enabled=False),
        llm_api=LocalLLMAdapter(),
        context_api=context_api,
        memory_api=memory_api,
        _build_context=_build_context,
        _debit_tokens=lambda *args, **kwargs: None,
    )


def test_extract_failure_memories_stages_correction_and_meta_rule_preference(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)
    logger = _Logger()
    state = _state()

    candidate_ids = extract_failure_memories(
        runner,
        state=state,
        action_result=ActionResult(
            command_id="cmd-1",
            status="failed",
            summary="web.search failed",
            error=ActionError(code="AUTH_REQUIRED", message="auth missing"),
        ),
        termination_reason="tool_failure_no_recovery",
        logger=logger,
        outcome_snapshot={
            "tool_results": [
                {
                    "tool_name": "web.search",
                    "args_signature": '{"query":"sf weather"}',
                }
            ],
            "correction_history": [
                {
                    "iteration_index": 1,
                    "correction_type": "retry_same",
                    "diagnosis_summary": "auth missing",
                    "applied": True,
                }
            ],
            "loop_iteration": 2,
        },
    )

    assert len(candidate_ids) == 3
    lines = (
        (tmp_path / "memory" / "memory.jsonl").read_text(encoding="utf-8").splitlines()
    )
    payloads = [json.loads(line) for line in lines if line.strip()]
    records = [item for item in payloads if item.get("kind") == "record"]
    candidates = [item for item in payloads if item.get("kind") == "candidate"]

    assert len(records) == 1
    assert records[0]["record_type"] == "strategy_outcome"
    assert records[0]["content"]["strategy_id"] == "research"
    assert records[0]["content"]["outcome_status"] == "failure"
    assert records[0]["content"]["termination_reason"] == "tool_failure_no_recovery"
    assert len(candidates) == 2
    correction = next(
        item for item in candidates if item["record_type"] == "correction"
    )
    assert correction["content"]["tool_name"] == "web.search"
    assert correction["content"]["args_signature"] == '{"query":"sf weather"}'
    assert correction["meta"]["source_failure_path"] is True
    assert correction["meta"]["source_termination_reason"] == "tool_failure_no_recovery"

    preference = next(
        item for item in candidates if item["record_type"] == "meta_rule_preference"
    )
    assert preference["meta"]["source_meta_rule_preference"] is True
    assert preference["meta"]["source_failure_path"] is True
    assert any(
        event_type == "brain.failure_memory.started"
        for event_type, _payload, _kwargs in logger.events
    )
    assert any(
        event_type == "brain.failure_memory.completed"
        for event_type, _payload, _kwargs in logger.events
    )


def test_extract_failure_memories_skips_without_trace(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    logger = _Logger()
    state = WorkingState(
        session_id="failure-session-empty",
        agent_id="failure-agent",
        goal="Check the weather via web.search",
        budgets_remaining=BudgetCounters(
            ticks=5,
            tool_calls=5,
            a2a_calls=0,
            tokens=5000,
            time_ms=60_000,
        ),
        trace_id="trace-failure-empty",
    )

    candidate_ids = extract_failure_memories(
        runner,
        state=state,
        action_result=None,
        termination_reason="circular_pattern",
        logger=logger,
    )

    assert candidate_ids == []
    assert any(
        event_type == "brain.failure_memory.skipped"
        and payload["reason"] == "no_failure_trace"
        for event_type, payload, _kwargs in logger.events
    )
