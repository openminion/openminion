from __future__ import annotations

from openminion.services.gateway.turn_intent import (
    BenchmarkHarnessTurnIntent,
    FreeformChatTurnIntent,
    MissionRunnerTurnIntent,
    ScriptedCliTurnIntent,
    TuiTaskTurnIntent,
    build_fail_closed_terminal_resolution,
    parse_typed_turn_intent,
    resolve_typed_goal,
)


class _StubSessionApi:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def append_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict,
        **_: object,
    ) -> str:
        self.events.append((event_type, dict(payload)))
        return f"event-{len(self.events)}"


def _benchmark_intent() -> BenchmarkHarnessTurnIntent:
    return BenchmarkHarnessTurnIntent(
        goal_id="goal-1",
        corpus_task_id="ameb-coding-01",
        description="Run one benchmark task",
        mission_type="coding",
        success_criteria=(
            {
                "criterion_id": "c1",
                "description": "tests pass",
                "structural_check": "success_criteria.tests_passed=true",
            },
        ),
        deliverables=(
            {
                "deliverable_id": "d1",
                "description": "patch artifact",
                "verification_hint": "artifact_presence",
            },
        ),
        failure_conditions=(
            {
                "condition_id": "f1",
                "kind": "success_criterion_unmet",
                "description": "tests did not pass",
            },
        ),
    )


def test_parse_typed_turn_intent_accepts_closed_set_record() -> None:
    parsed = parse_typed_turn_intent(
        {
            "kind": "benchmark_harness",
            "goal_id": "goal-1",
            "corpus_task_id": "ameb-coding-01",
            "description": "Run one benchmark task",
            "mission_type": "coding",
            "success_criteria": [
                {
                    "criterion_id": "c1",
                    "description": "tests pass",
                    "structural_check": "success_criteria.tests_passed=true",
                }
            ],
            "deliverables": [
                {
                    "deliverable_id": "d1",
                    "description": "patch artifact",
                    "verification_hint": "artifact_presence",
                }
            ],
        }
    )
    assert isinstance(parsed, BenchmarkHarnessTurnIntent)
    assert parsed.kind == "benchmark_harness"


def test_resolve_typed_goal_returns_none_for_freeform_chat() -> None:
    assert resolve_typed_goal(FreeformChatTurnIntent(kind="freeform_chat")) is None


def test_resolve_typed_goal_is_total_over_structured_kinds() -> None:
    intents = (
        MissionRunnerTurnIntent(
            kind="mission_runner",
            goal_id="goal-mission",
            mission_id="mission-1",
            description="Run mission",
            mission_type="operations",
            success_criteria=(
                {
                    "criterion_id": "c1",
                    "description": "recover state",
                    "structural_check": "success_criteria.state_recovered=true",
                },
            ),
            deliverables=(
                {
                    "deliverable_id": "d1",
                    "description": "health artifact",
                    "verification_hint": "artifact_presence",
                },
            ),
        ),
        _benchmark_intent(),
        ScriptedCliTurnIntent(
            kind="scripted_cli",
            goal_id="goal-cli",
            command_name="openminion mission run",
            description="CLI benchmark",
            mission_type="research",
            success_criteria=(
                {
                    "criterion_id": "c1",
                    "description": "source count",
                    "structural_check": "success_criteria.source_count_ge_2=true",
                },
            ),
            deliverables=(
                {
                    "deliverable_id": "d1",
                    "description": "findings",
                    "verification_hint": "artifact_presence",
                },
            ),
        ),
        TuiTaskTurnIntent(
            kind="tui_task",
            goal_id="goal-tui",
            task_id="task-1",
            description="TUI task",
            mission_type="coding",
            success_criteria=(
                {
                    "criterion_id": "c1",
                    "description": "tests pass",
                    "structural_check": "success_criteria.tests_passed=true",
                },
            ),
            deliverables=(
                {
                    "deliverable_id": "d1",
                    "description": "patch",
                    "verification_hint": "artifact_presence",
                },
            ),
        ),
    )
    for intent in intents:
        goal = resolve_typed_goal(intent)
        assert goal is not None
        assert goal.goal_id == intent.goal_id
        assert goal.description == intent.description
        assert len(goal.success_criteria) == len(intent.success_criteria)
        assert len(goal.deliverables) == len(intent.deliverables)


def test_build_fail_closed_terminal_resolution_returns_none_for_freeform_chat() -> None:
    resolved = build_fail_closed_terminal_resolution(
        turn_intent=FreeformChatTurnIntent(kind="freeform_chat"),
        run_id="run-1",
        session_id="sess-1",
        agent_id="agent-1",
        session_api=_StubSessionApi(),
    )
    assert resolved is None


def test_build_fail_closed_terminal_resolution_returns_typed_tuple_for_benchmark() -> (
    None
):
    resolved = build_fail_closed_terminal_resolution(
        turn_intent=_benchmark_intent(),
        run_id="run-1",
        session_id="sess-1",
        agent_id="agent-1",
        session_api=_StubSessionApi(),
    )
    assert resolved is not None
    run, goal, verifier_results, fired_failure_conditions = resolved
    assert run.run_id == "run-1"
    assert goal.goal_id == "goal-1"
    assert len(verifier_results) == 2
    assert fired_failure_conditions == ()
    assert all(not row.passed for row in verifier_results)
