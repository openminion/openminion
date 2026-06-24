from __future__ import annotations

from openminion.modules.brain.bootstrap.resolve import (
    apply_resolved_act_route,
    resolve_working_act_route,
)
from openminion.modules.brain.execution.public_taxonomy import (
    public_surface_payload_for_state,
)
from openminion.modules.brain.schemas import (
    ActDecision,
    BudgetCounters,
    ToolCommand,
    WorkingState,
)


def _state(session_id: str = "runtime-route") -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="router-agent",
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=5,
            a2a_calls=5,
            tokens=1000,
            time_ms=10_000,
        ),
    )


def test_missing_profile_and_execution_target_default_to_general_local() -> None:
    decision = ActDecision(mode="act")
    state = _state()

    route = resolve_working_act_route(
        decision=decision,
        state=state,
        default_act_profile=None,
        has_new_user_input=True,
    )

    assert route.act_profile == "general"
    assert route.execution_target.kind == "local"
    assert route.source == "runtime_default_general"


def test_fixed_profile_outranks_conflicting_decide_profile() -> None:
    decision = ActDecision(mode="act", act_profile="general")
    state = _state("runtime-route-fixed")

    route = resolve_working_act_route(
        decision=decision,
        state=state,
        default_act_profile="research",
        has_new_user_input=True,
    )

    assert route.act_profile == "research"
    assert route.execution_target.kind == "local"
    assert route.source == "config_default_act_profile"


def test_explicit_subtasks_force_orchestrate_when_profile_unset() -> None:
    decision = ActDecision(
        mode="act",
        subtasks=[
            {"subtask_id": "1", "goal": "research flights"},
            {"subtask_id": "2", "goal": "compare hotel options"},
        ],
    )
    state = _state("runtime-route-subtasks")

    route = resolve_working_act_route(
        decision=decision,
        state=state,
        default_act_profile=None,
        has_new_user_input=True,
    )

    assert route.act_profile == "orchestrate"
    assert route.execution_target.kind == "local"
    assert route.source == "decision_subtasks"


def test_single_trivial_subtask_does_not_force_orchestrate() -> None:
    decision = ActDecision(
        mode="act",
        subtasks=[{"subtask_id": "1", "goal": "get current UTC time"}],
    )
    state = _state("runtime-route-single-subtask")

    route = resolve_working_act_route(
        decision=decision,
        state=state,
        default_act_profile=None,
        has_new_user_input=True,
    )

    assert route.act_profile == "general"
    assert route.execution_target.kind == "local"
    assert route.source == "runtime_default_general"


def test_explicit_delegated_execution_target_wins() -> None:
    decision = ActDecision(
        mode="act",
        execution_target={"kind": "delegated", "target_agent_id": "kimi"},
    )
    state = _state("runtime-route-delegated")

    route = resolve_working_act_route(
        decision=decision,
        state=state,
        default_act_profile=None,
        has_new_user_input=True,
    )

    assert route.execution_target.kind == "delegated"
    assert route.execution_target.target_agent_id == "kimi"
    assert route.source == "decision_execution_target"


def test_persisted_working_route_reused_for_resume_without_new_input() -> None:
    state = _state("runtime-route-resume")
    state.working_act_profile = "coding"
    state.working_execution_target_kind = "local"
    decision = ActDecision(mode="act", reason_code="resume_existing_plan")

    route = resolve_working_act_route(
        decision=decision,
        state=state,
        default_act_profile=None,
        has_new_user_input=False,
    )

    assert route.act_profile == "coding"
    assert route.execution_target.kind == "local"
    assert route.source == "resume_checkpoint"


def test_public_surface_prefers_working_route_over_internal_mode_mapping() -> None:
    state = _state("runtime-route-public")
    state.active_mode_name = "act_loop_adaptive"
    state.working_act_profile = "research"
    state.working_execution_target_kind = "local"
    decision = ActDecision(mode="act")
    route = resolve_working_act_route(
        decision=decision,
        state=state,
        default_act_profile=None,
        has_new_user_input=False,
    )
    apply_resolved_act_route(decision=decision, route=route)

    payload = public_surface_payload_for_state(state)

    assert payload["mode_name"] == "act"
    assert payload["act_profile"] == "research"
    assert payload["execution_target"] == "local"


def test_seeded_confirmation_replay_does_not_reenter_research_profile() -> None:
    state = _state("runtime-route-seeded-confirm-research")
    state.working_act_profile = "research"
    state.working_execution_target_kind = "local"
    decision = ActDecision(mode="act", reason_code="confirmation_replay")
    decision._seeded_commands = [
        ToolCommand(
            title="run pytest",
            tool_name="exec.run",
            args={"command": "python -m pytest -q tests"},
        )
    ]

    route = resolve_working_act_route(
        decision=decision,
        state=state,
        default_act_profile=None,
        has_new_user_input=True,
    )

    assert route.act_profile == "general"
    assert route.execution_target.kind == "local"
    assert route.source == "confirmation_replay_seeded_general"
