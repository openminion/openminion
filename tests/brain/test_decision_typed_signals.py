from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openminion.modules.brain.execution.entry import _sync_typed_decision_signals
from openminion.modules.brain.loop.context.pending_turn import (
    sync_pending_turn_context_from_decision,
)
from openminion.modules.brain.schemas import DecisionAdapter


class _MemoryAPI:
    def __init__(self) -> None:
        self.candidates: list[dict[str, object]] = []

    def stage_candidate(self, **kwargs: object) -> str:
        self.candidates.append(dict(kwargs))
        return "candidate-1"


def test_decision_schema_accepts_group_a_typed_signal_fields() -> None:
    decision = DecisionAdapter.validate_python(
        {
            "route": "respond",
            "confidence": 0.9,
            "respond_kind": "answer",
            "answer": "done",
            "pending_turn_context": {
                "original_user_request": "book travel",
                "active_work_summary": "offered hotel list",
            },
            "confident_complete": {
                "complete": True,
                "reasoning": "Final answer provided.",
            },
            "session_work_summary": {"summary": "Planned Japan travel."},
            "meta_rule_preference": {
                "rule": "retry_limit",
                "preferred_value": 2,
                "reasoning": "Avoid repeated loops.",
            },
            "delegation_context": {"summary": "Parent needs hotel options."},
            "delegation_result_summary": {
                "summary": "Found options.",
                "status": "complete",
            },
        }
    )

    assert decision.pending_turn_context is not None
    assert decision.confident_complete is not None
    assert decision.session_work_summary is not None
    assert decision.meta_rule_preference is not None
    assert decision.delegation_context is not None
    assert decision.delegation_result_summary is not None


def test_decision_schema_does_not_accept_task_plan_field() -> None:
    with pytest.raises(ValidationError):
        DecisionAdapter.validate_python(
            {
                "route": "respond",
                "confidence": 0.9,
                "respond_kind": "answer",
                "answer": "done",
                "task_plan": {"plan_id": "p1", "objective": "x", "steps": []},
            }
        )


def test_typed_decision_signal_sync_updates_session_work_summary() -> None:
    decision = DecisionAdapter.validate_python(
        {
            "route": "respond",
            "confidence": 0.9,
            "respond_kind": "answer",
            "answer": "done",
            "session_work_summary": {"summary": "Japan plan is ready."},
        }
    )
    state = SimpleNamespace(session_work_summary=None)

    _sync_typed_decision_signals(
        runner=SimpleNamespace(memory_api=None),
        state=state,
        decision=decision,
    )

    assert state.session_work_summary == "Japan plan is ready."


def test_typed_decision_signal_sync_stages_meta_rule_preference() -> None:
    decision = DecisionAdapter.validate_python(
        {
            "route": "respond",
            "confidence": 0.9,
            "respond_kind": "answer",
            "answer": "done",
            "meta_rule_preference": {
                "rule": "max_search_attempts",
                "preferred_value": 2,
                "reasoning": "The user wants shorter loops.",
            },
        }
    )
    memory_api = _MemoryAPI()
    state = SimpleNamespace(memory_candidates=[])

    _sync_typed_decision_signals(
        runner=SimpleNamespace(
            memory_api=memory_api,
            profile=SimpleNamespace(agent_id="agent"),
        ),
        state=state,
        decision=decision,
    )

    assert state.memory_candidates == ["candidate-1"]
    assert memory_api.candidates[0]["record_type"] == "meta_rule_preference"
    assert memory_api.candidates[0]["content"]["preferred_value"] == 2


def test_pending_turn_context_decision_field_uses_existing_sync_owner() -> None:
    decision = DecisionAdapter.validate_python(
        {
            "route": "respond",
            "confidence": 0.9,
            "respond_kind": "answer",
            "answer": "I can do that next.",
            "pending_turn_context": {
                "original_user_request": "plan a Japan trip",
                "active_work_summary": "offered to choose hotels next",
            },
        }
    )
    state = SimpleNamespace(
        pending_turn_context=None,
        pending_turn_context_stale_turns=2,
    )

    sync_pending_turn_context_from_decision(
        state=state,
        decision=decision,
        user_input="yes, choose hotels",
    )

    assert state.pending_turn_context is not None
    assert state.pending_turn_context.original_user_request == "plan a Japan trip"
    assert state.pending_turn_context_stale_turns == 0


def test_confident_complete_decision_field_remains_available_to_completion_owner() -> (
    None
):
    decision = DecisionAdapter.validate_python(
        {
            "route": "respond",
            "confidence": 0.9,
            "respond_kind": "answer",
            "answer": "All requested work is complete.",
            "confident_complete": {
                "complete": True,
                "reasoning": "The final answer satisfies the request.",
            },
        }
    )

    assert decision.confident_complete is not None
    assert decision.confident_complete.complete is True
    assert decision.confident_complete.reasoning == (
        "The final answer satisfies the request."
    )


def test_delegation_typed_decision_fields_remain_available_to_downstream_handlers() -> (
    None
):
    decision = DecisionAdapter.validate_python(
        {
            "route": "act",
            "confidence": 0.9,
            "act_profile": "general",
            "execution_target": {
                "kind": "delegated",
                "target_capability": "hotel_research",
            },
            "delegation_context": {
                "summary": "Child should research hotel options.",
                "artifacts": ["trip-outline.md"],
            },
            "delegation_result_summary": {
                "summary": "Child found three hotel groups.",
                "status": "complete",
                "artifacts_produced": ["hotels.md"],
            },
        }
    )

    assert decision.delegation_context is not None
    assert decision.delegation_context.summary == "Child should research hotel options."
    assert decision.delegation_result_summary is not None
    assert decision.delegation_result_summary.status == "complete"
