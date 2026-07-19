from __future__ import annotations

import pytest
from pydantic import ValidationError

from openminion.modules.brain.config import BrainConfig, RequestHandoffConfig, RunnerOptions
from openminion.modules.brain.adapters.llm.request import (
    _build_request_readiness_guidance_message,
)
from openminion.modules.brain.schemas import (
    ActDecision,
    AgentBudgets,
    BudgetCounters,
    Plan,
    RequestAssumption,
    RequestReadiness,
    RespondDecision,
    ToolCommand,
    WorkingState,
)


def _budgets() -> AgentBudgets:
    return AgentBudgets(
        max_ticks_per_user_turn=4,
        max_tool_calls=2,
        max_a2a_calls=0,
        max_total_llm_tokens=2000,
        max_elapsed_ms=10000,
    )


def test_request_readiness_accepts_bounded_execute_ready_payload() -> None:
    assumption = RequestAssumption(
        text="Use the existing test fixture.",
        source="repository",
        reversible=True,
        validation_trigger="Focused pytest fails.",
    )

    decision = ActDecision(
        request_readiness=RequestReadiness(
            posture="brief_plan",
            requested_outcome="execute",
            state="ready",
            assumptions=[assumption],
        )
    )

    assert decision.request_readiness is not None
    assert decision.request_readiness.assumptions[0].source == "repository"


def test_omitted_request_readiness_preserves_legacy_decision_shape() -> None:
    decision = RespondDecision(respond_kind="answer", answer="legacy answer")

    assert decision.request_readiness is None


def test_execute_ready_must_route_to_act() -> None:
    with pytest.raises(ValidationError, match="execute \\+ ready"):
        RespondDecision(
            respond_kind="answer",
            answer="no action",
            request_readiness={
                "posture": "direct",
                "requested_outcome": "execute",
                "state": "ready",
            },
        )


def test_clarify_requires_needs_user_readiness_state() -> None:
    with pytest.raises(ValidationError, match="needs_user"):
        RespondDecision(
            respond_kind="clarify",
            question="Which environment?",
            request_readiness={
                "posture": "direct",
                "requested_outcome": "execute",
                "state": "blocked",
            },
        )


def test_needs_plan_review_requires_review_posture() -> None:
    with pytest.raises(ValidationError, match="needs_plan_review"):
        RequestReadiness(
            posture="brief_plan",
            requested_outcome="execute",
            state="needs_plan_review",
        )


def test_assumption_count_and_lengths_are_bounded() -> None:
    assumptions = [
        {
            "text": f"assumption {index}",
            "source": "reversible_default",
            "reversible": True,
            "validation_trigger": "user corrects it",
        }
        for index in range(6)
    ]

    with pytest.raises(ValidationError):
        RequestReadiness(
            posture="direct",
            requested_outcome="answer_only",
            state="ready",
            assumptions=assumptions,
        )


def test_working_state_persists_single_request_readiness_copy() -> None:
    state = WorkingState(
        session_id="s-hlpe",
        agent_id="agent",
        budgets_remaining=BudgetCounters(
            ticks=4, tool_calls=2, a2a_calls=0, tokens=2000, time_ms=10000
        ),
        request_readiness={
            "posture": "direct",
            "requested_outcome": "plan_only",
            "state": "ready",
        },
    )

    dumped = state.model_dump(mode="json")
    assert dumped["request_readiness"]["requested_outcome"] == "plan_only"
    assert WorkingState.model_validate(dumped).request_readiness == state.request_readiness


def test_runner_and_brain_config_default_handoff_disabled() -> None:
    config = BrainConfig(agent_id="agent", budgets=_budgets())

    assert config.request_handoff == RequestHandoffConfig(enabled=False)
    assert RunnerOptions().request_handoff_enabled is False


def test_llm_decision_guidance_names_request_readiness_contract() -> None:
    guidance = _build_request_readiness_guidance_message(
        purpose="decide",
        schema=type("Decision", (), {}),
    )

    assert "Decision.request_readiness" in guidance
    assert "answer_only" in guidance
    assert _build_request_readiness_guidance_message(
        purpose="act",
        schema=type("Decision", (), {}),
    ) == ""


def test_plan_exposes_first_structural_action_without_prose_parsing() -> None:
    command = ToolCommand(
        title="Read file",
        tool_name="file.read",
        args={"path": "README.md"},
        success_criteria={"status": "success"},
    )
    plan = Plan(objective="inspect", steps=[command])

    assert plan.first_executable_step() == command
