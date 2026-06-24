from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.runtime.memory import (
    apply_improvements,
    apply_failure_memories,
    apply_success_memories,
    stage_strategy_outcome,
)
from openminion.modules.brain.schemas import (
    FailureMemoryReport,
    FixItem,
    MetaRulePreference,
    ReflectReport,
    SuccessMemoryConfig,
    SuccessMemoryReport,
    WorkingState,
)


class _MemoryAPIStub:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, object]] = []
        self.stage_calls: list[dict[str, object]] = []

    def put_record(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: dict[str, object] | str,
        tags: list[str],
        evidence_refs: list[str],
    ) -> str:
        self.put_calls.append(
            {
                "scope": scope,
                "record_type": record_type,
                "title": title,
                "content": content,
                "tags": list(tags),
                "evidence_refs": list(evidence_refs),
            }
        )
        return "rec-1"

    def stage_candidate(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: dict[str, object] | str,
        tags: list[str],
        evidence_refs: list[str],
        confidence: float | None = None,
        meta: dict[str, object] | None = None,
    ) -> str:
        self.stage_calls.append(
            {
                "scope": scope,
                "record_type": record_type,
                "title": title,
                "content": content,
                "tags": list(tags),
                "evidence_refs": list(evidence_refs),
                "confidence": confidence,
                "meta": dict(meta or {}),
            }
        )
        return "cand-1"


class _LoggerStub:
    def emit(self, *_args, **_kwargs) -> None:
        return None


def test_apply_improvements_normalizes_lesson_record_type() -> None:
    memory_api = _MemoryAPIStub()
    runner = SimpleNamespace(
        memory_api=memory_api,
        profile=SimpleNamespace(
            agent_id="agent-test",
            defaults=SimpleNamespace(
                auto_save_lessons=True, auto_stage_policy_candidates=False
            ),
        ),
    )
    state = WorkingState(
        session_id="s-1",
        agent_id="agent-test",
        budgets_remaining={
            "ticks": 1,
            "tool_calls": 1,
            "a2a_calls": 0,
            "tokens": 100,
            "time_ms": 1000,
        },
    )
    report = ReflectReport(
        session_id="s-1",
        agent_id="agent-test",
        command_id="cmd-1",
        outcome="failure",
        root_cause="tool failed",
        fixes=[
            FixItem(
                kind="lesson",
                title="Do not repeat failing search",
                content={"reason": "runtime_error"},
                confidence=0.8,
            )
        ],
    )

    apply_improvements(runner, state=state, report=report, logger=_LoggerStub())

    assert len(memory_api.put_calls) == 1
    call = memory_api.put_calls[0]
    assert call["record_type"] == "fact"
    assert "self-improvement:lesson" in call["tags"]


def test_apply_improvements_skips_unsupported_candidate_fix_kinds_without_raising() -> (
    None
):
    memory_api = _MemoryAPIStub()
    runner = SimpleNamespace(
        memory_api=memory_api,
        profile=SimpleNamespace(
            agent_id="agent-test",
            defaults=SimpleNamespace(
                auto_save_lessons=True, auto_stage_policy_candidates=True
            ),
        ),
    )
    state = WorkingState(
        session_id="s-unsupported-candidate",
        agent_id="agent-test",
        budgets_remaining={
            "ticks": 1,
            "tool_calls": 1,
            "a2a_calls": 0,
            "tokens": 100,
            "time_ms": 1000,
        },
    )
    report = ReflectReport(
        session_id="s-unsupported-candidate",
        agent_id="agent-test",
        command_id="cmd-unsupported-candidate",
        outcome="failure",
        root_cause="tool validation failed",
        fixes=[
            FixItem(
                kind="tool_wrapper_change",
                title="Wrap invalid tool arguments before reflection",
                content={"command": "weather"},
                confidence=0.7,
            )
        ],
    )

    apply_improvements(runner, state=state, report=report, logger=_LoggerStub())

    assert memory_api.stage_calls == []
    assert (
        "Guardrail: Wrap invalid tool arguments before reflection" in state.constraints
    )


def test_apply_improvements_normalizes_pin_candidate_to_pin_memory_type() -> None:
    memory_api = _MemoryAPIStub()
    runner = SimpleNamespace(
        memory_api=memory_api,
        profile=SimpleNamespace(
            agent_id="agent-test",
            defaults=SimpleNamespace(
                auto_save_lessons=True, auto_stage_policy_candidates=True
            ),
        ),
    )
    state = WorkingState(
        session_id="s-pin-candidate",
        agent_id="agent-test",
        budgets_remaining={
            "ticks": 1,
            "tool_calls": 1,
            "a2a_calls": 0,
            "tokens": 100,
            "time_ms": 1000,
        },
    )
    report = ReflectReport(
        session_id="s-pin-candidate",
        agent_id="agent-test",
        command_id="cmd-pin-candidate",
        outcome="failure",
        root_cause="tool produced reusable note",
        fixes=[
            FixItem(
                kind="pin_candidate",
                title="Pin the successful fetch workflow",
                content={"tool": "web.fetch"},
                confidence=0.8,
                tags=["candidate"],
            )
        ],
    )

    apply_improvements(runner, state=state, report=report, logger=_LoggerStub())

    assert len(memory_api.stage_calls) == 1
    call = memory_api.stage_calls[0]
    assert call["record_type"] == "pin"
    assert "candidate_kind:pin_candidate" in call["tags"]


def test_apply_success_memories_stages_candidates_with_provenance() -> None:
    memory_api = _MemoryAPIStub()
    runner = SimpleNamespace(
        memory_api=memory_api,
        profile=SimpleNamespace(
            agent_id="agent-test",
            success_memory=SuccessMemoryConfig(enabled=True),
        ),
        options=SimpleNamespace(
            success_memory_config=SuccessMemoryConfig(enabled=True)
        ),
    )
    state = WorkingState(
        session_id="s-success",
        agent_id="agent-test",
        budgets_remaining={
            "ticks": 1,
            "tool_calls": 1,
            "a2a_calls": 0,
            "tokens": 100,
            "time_ms": 1000,
        },
    )
    report = SuccessMemoryReport(
        session_id="s-success",
        agent_id="agent-test",
        command_ids=["cmd-1"],
        items=[
            {
                "kind": "procedure",
                "title": "Procedure from success",
                "content": {"steps": ["echo"]},
                "confidence": 0.9,
                "tags": ["existing"],
            },
            {
                "kind": "tool_habit",
                "title": "Habit from success",
                "content": {"tool": "echo"},
                "confidence": 0.82,
            },
        ],
    )

    result = apply_success_memories(
        runner,
        state=state,
        report=report,
        logger=_LoggerStub(),
        provenance_meta={"source_trace_id": "trace-1"},
    )

    assert result["candidate_ids"] == ["cand-1", "cand-1"]
    assert len(memory_api.stage_calls) == 2
    assert state.memory_candidates == ["cand-1", "cand-1"]
    first = memory_api.stage_calls[0]
    assert first["scope"] == "agent:agent-test"
    assert first["record_type"] == "procedure"
    assert first["confidence"] == 0.9
    assert "success_path" in first["tags"]
    assert first["meta"]["source_trace_id"] == "trace-1"
    assert first["meta"]["source_success_path"] is True


def test_apply_success_memories_respects_thresholds_and_kind_toggles() -> None:
    memory_api = _MemoryAPIStub()
    config = SuccessMemoryConfig(
        enabled=True,
        procedure_enabled=False,
        tool_habit_enabled=True,
        min_item_confidence=0.8,
    )
    runner = SimpleNamespace(
        memory_api=memory_api,
        profile=SimpleNamespace(
            agent_id="agent-test",
            success_memory=config,
        ),
        options=SimpleNamespace(success_memory_config=config),
    )
    state = WorkingState(
        session_id="s-skip",
        agent_id="agent-test",
        budgets_remaining={
            "ticks": 1,
            "tool_calls": 1,
            "a2a_calls": 0,
            "tokens": 100,
            "time_ms": 1000,
        },
    )
    report = SuccessMemoryReport(
        session_id="s-skip",
        agent_id="agent-test",
        command_ids=["cmd-1"],
        items=[
            {
                "kind": "procedure",
                "title": "Disabled procedure",
                "content": {"steps": ["echo"]},
                "confidence": 0.95,
            },
            {
                "kind": "tool_habit",
                "title": "Low confidence habit",
                "content": {"tool": "echo"},
                "confidence": 0.5,
            },
        ],
    )

    result = apply_success_memories(
        runner,
        state=state,
        report=report,
        logger=_LoggerStub(),
    )

    assert result["candidate_ids"] == []
    assert len(result["skipped_items"]) == 2
    assert memory_api.stage_calls == []


def test_apply_success_memories_attaches_trace_rationale_when_present() -> None:
    memory_api = _MemoryAPIStub()
    runner = SimpleNamespace(
        memory_api=memory_api,
        profile=SimpleNamespace(
            agent_id="agent-test",
            success_memory=SuccessMemoryConfig(enabled=True),
        ),
        options=SimpleNamespace(
            success_memory_config=SuccessMemoryConfig(enabled=True)
        ),
    )
    state = WorkingState(
        session_id="s-success-rationale",
        agent_id="agent-test",
        budgets_remaining={
            "ticks": 1,
            "tool_calls": 1,
            "a2a_calls": 0,
            "tokens": 100,
            "time_ms": 1000,
        },
    )
    report = SuccessMemoryReport(
        session_id="s-success-rationale",
        agent_id="agent-test",
        command_ids=["cmd-1"],
        items=[
            {
                "kind": "procedure",
                "title": "Procedure from success",
                "content": {"steps": ["echo"]},
                "confidence": 0.9,
            }
        ],
    )

    apply_success_memories(
        runner,
        state=state,
        report=report,
        logger=_LoggerStub(),
        provenance_meta={"source_thinking_rationale": "Use the successful tool flow."},
    )

    first = memory_api.stage_calls[0]
    assert first["meta"]["rationale"] == "Use the successful tool flow."
    assert first["content"]["rationale"] == "Use the successful tool flow."


def test_apply_failure_memories_stages_correction_and_meta_rule_preference() -> None:
    memory_api = _MemoryAPIStub()
    runner = SimpleNamespace(
        memory_api=memory_api,
        profile=SimpleNamespace(agent_id="agent-test"),
    )
    state = WorkingState(
        session_id="s-failure",
        agent_id="agent-test",
        budgets_remaining={
            "ticks": 1,
            "tool_calls": 1,
            "a2a_calls": 0,
            "tokens": 100,
            "time_ms": 1000,
        },
    )
    report = FailureMemoryReport(
        session_id="s-failure",
        agent_id="agent-test",
        termination_reason="tool_failure_no_recovery",
        command_ids=["cmd-1"],
        items=[
            {
                "kind": "correction",
                "title": "Correction for web.search",
                "content": {
                    "text": "Before retrying web.search, verify auth first.",
                    "tool_name": "web.search",
                    "args_signature": '{"query":"sf weather"}',
                },
                "confidence": 0.84,
                "tags": ["existing"],
            }
        ],
        meta_rule_preference=MetaRulePreference(
            rule="web.search.retry_strategy",
            preferred_value="verify_precondition_first",
            reasoning="web.search timed out without auth.",
        ),
    )

    result = apply_failure_memories(
        runner,
        state=state,
        report=report,
        logger=_LoggerStub(),
        provenance_meta={"source_trace_id": "trace-failure-1"},
    )

    assert result["candidate_ids"] == ["cand-1"]
    assert result["meta_rule_preference_candidate_id"] == "cand-1"
    assert state.memory_candidates == ["cand-1", "cand-1"]
    assert len(memory_api.stage_calls) == 2
    correction_call = memory_api.stage_calls[0]
    assert correction_call["scope"] == "agent:agent-test"
    assert correction_call["record_type"] == "correction"
    assert correction_call["confidence"] == 0.84
    assert "failure_path" in correction_call["tags"]
    assert correction_call["meta"]["source_failure_path"] is True
    assert correction_call["meta"]["source_trace_id"] == "trace-failure-1"
    preference_call = memory_api.stage_calls[1]
    assert preference_call["record_type"] == "meta_rule_preference"
    assert preference_call["meta"]["source_meta_rule_preference"] is True
    assert preference_call["meta"]["source_failure_path"] is True


def test_stage_strategy_outcome_writes_typed_structural_record() -> None:
    memory_api = _MemoryAPIStub()
    runner = SimpleNamespace(
        memory_api=memory_api,
        profile=SimpleNamespace(agent_id="agent-test"),
    )
    state = SimpleNamespace(
        session_id="s-strategy-outcome",
        agent_id="agent-test",
        trace_id="trace-strategy-outcome",
        turn_index=4,
        decision_context_recorded_at="2026-05-08T00:00:00+00:00",
    )

    result = stage_strategy_outcome(
        runner,
        state=state,
        strategy_id="research",
        capability_category="live_information",
        intent_category="latest_news",
        outcome_status="failure",
        provenance_meta={"source_termination_reason": "budget_exhausted"},
    )

    assert result == {"record_id": "rec-1", "skipped_reason": None}
    assert len(memory_api.put_calls) == 1
    call = memory_api.put_calls[0]
    assert call["scope"] == "agent:agent-test"
    assert call["record_type"] == "strategy_outcome"
    assert call["title"] == "strategy_outcome:research:failure:latest_news"
    assert "strategy_outcome" in call["tags"]
    assert "strategy_id:research" in call["tags"]
    assert "capability_category:live_information" in call["tags"]
    assert "intent_category:latest_news" in call["tags"]
    assert "outcome_status:failure" in call["tags"]
    content = call["content"]
    assert content["strategy_id"] == "research"
    assert content["capability_category"] == "live_information"
    assert content["intent_category"] == "latest_news"
    assert content["outcome_status"] == "failure"
    assert content["termination_reason"] == "budget_exhausted"
