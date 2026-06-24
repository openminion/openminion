from __future__ import annotations

import json

from openminion.modules.brain.execution.intent_state import (
    build_raw_intent_execution_state_block,
)
from openminion.modules.brain.execution.skill_binding import activate_skill_for_command
from openminion.modules.brain.schemas import (
    BudgetCounters,
    SubIntent,
    ToolCommand,
    WorkingState,
)


def _state_with_skills() -> WorkingState:
    return WorkingState(
        session_id="scc-session",
        agent_id="agent",
        budgets_remaining=BudgetCounters(
            ticks=5,
            tool_calls=3,
            a2a_calls=1,
            tokens=1000,
            time_ms=10000,
        ),
        active_skill_ids=["alpha", "beta"],
        resolved_skill_ids=["alpha", "beta"],
        resolved_skill_versions={"alpha": "a" * 64, "beta": "b" * 64},
        decision_sub_intent_refs=[
            SubIntent(
                id="intent_alpha",
                description="alpha-bound work",
                skill_id="alpha",
            ),
            SubIntent(
                id="intent_beta",
                description="beta-bound work",
                skill_id="beta",
            ),
        ],
    )


def test_command_skill_id_explicitly_activates_matching_skill() -> None:
    state = _state_with_skills()
    command = ToolCommand(
        title="run beta step",
        tool_name="echo",
        args={"msg": "ok"},
        success_criteria={"status": "success"},
        skill_id="beta",
    )

    assert activate_skill_for_command(state, command) == "beta"
    assert state.active_skill_id == "beta"
    assert state.active_skill_version_hash == "b" * 64


def test_sub_intent_skill_id_activates_only_explicit_single_binding() -> None:
    state = _state_with_skills()
    command = ToolCommand(
        title="run beta intent",
        tool_name="echo",
        args={"msg": "ok"},
        success_criteria={"status": "success"},
        sub_intent_ids=["intent_beta"],
    )

    assert activate_skill_for_command(state, command) == "beta"
    assert state.active_skill_id == "beta"


def test_ambiguous_or_unknown_binding_falls_back_to_primary_skill() -> None:
    state = _state_with_skills()
    command = ToolCommand(
        title="run ambiguous intent",
        tool_name="echo",
        args={"msg": "ok"},
        success_criteria={"status": "success"},
        sub_intent_ids=["intent_alpha", "intent_beta"],
        skill_id="missing",
    )

    assert activate_skill_for_command(state, command) == "alpha"
    assert state.active_skill_id == "alpha"
    assert state.active_skill_version_hash == "a" * 64


def test_intent_execution_state_block_carries_skill_id() -> None:
    state = _state_with_skills()

    block = build_raw_intent_execution_state_block(state.intent_execution_states)

    assert block is not None
    payload = json.loads(block.removeprefix("intent_execution_states="))
    assert payload[0]["skill_id"] == "alpha"
    assert payload[1]["skill_id"] == "beta"
