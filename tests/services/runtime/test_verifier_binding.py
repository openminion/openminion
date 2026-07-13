from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

from openminion.modules.brain.runtime.verification.policy import VerifierResult
from openminion.modules.brain.schemas import (
    Deliverable,
    FailureCondition,
    FailureConditionKind,
    Goal,
    SuccessCriterion,
)
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database
from openminion.services.runtime.run_status import (
    RUN_CHECKPOINT_EVENT_TYPE,
    RUN_STATE_COMPLETED,
    RUN_STATE_FAILED,
    RUN_TERMINAL_BLOCKED,
    RUN_TERMINAL_BUDGET_EXHAUSTED,
    RUN_TERMINAL_COMPLETED,
    RUN_TERMINAL_FAILED,
    RUN_TERMINAL_NEEDS_HUMAN,
    Run,
)
from openminion.services.runtime.verifier_binding import (
    TERMINAL_STATE_PROVENANCE_FIELD,
    TERMINAL_STATE_PROVENANCE_TYPED,
    bind_run_terminal_event,
    derive_run_terminal_state,
)


def test_runtime_verifier_binding_surface_is_canonical_brain_adapter() -> None:
    from openminion.services.brain.adapters.run_verification import (
        bind_run_terminal_event as canonical,
    )
    from openminion.services.runtime.verifier_binding import (
        bind_run_terminal_event as compatibility,
    )

    assert compatibility is canonical


def _goal(
    *,
    criterion_count: int = 1,
    deliverable_count: int = 1,
    failure_conditions: list[FailureCondition] | None = None,
) -> Goal:
    return Goal(
        goal_id="g1",
        description="placeholder",
        success_criteria=[
            SuccessCriterion(
                criterion_id=f"c{i}",
                description="placeholder",
                structural_check="artifact_present",
            )
            for i in range(criterion_count)
        ],
        deliverables=[
            Deliverable(
                deliverable_id=f"d{i}",
                description="placeholder",
                verification_hint="artifact_presence",
            )
            for i in range(deliverable_count)
        ],
        failure_conditions=failure_conditions or [],
    )


def _passing_result(target_id: str) -> VerifierResult:
    return VerifierResult(
        family="structural",
        goal_id="g1",
        run_id="r1",
        target_id=target_id,
        passed=True,
        reasons=[],
    )


def _failing_result(target_id: str) -> VerifierResult:
    return VerifierResult(
        family="structural",
        goal_id="g1",
        run_id="r1",
        target_id=target_id,
        passed=False,
        reasons=["structural-fail"],
    )


def _condition(kind: str) -> FailureCondition:
    return FailureCondition(
        condition_id=f"f-{kind}",
        kind=kind,  # type: ignore[arg-type]
        description="placeholder",
    )


def _run(session_id: str, *, goal_id: str = "g1") -> Run:
    return Run(
        run_id="r1",
        session_id=session_id,
        goal_id=goal_id,
        state="running",
    )


@pytest.fixture
def session_env(tmp_path: Path):
    db_path = tmp_path / "state" / "openminion.db"
    migrate_database(db_path)
    connection = connect_database(db_path)
    sessions = SessionStore(connection)
    session = sessions.resolve_session(
        agent_id="main",
        channel="console",
        target="alvb-bind-tests",
    )
    try:
        yield sessions, session
    finally:
        connection.close()


def test_all_passing_results_yields_completed() -> None:
    goal = _goal()
    results = [_passing_result("c0"), _passing_result("d0")]
    terminal = derive_run_terminal_state(goal, results)
    assert terminal == RUN_TERMINAL_COMPLETED


def test_missing_deliverable_pass_yields_failed() -> None:
    goal = _goal()
    results = [_passing_result("c0")]  # deliverable d0 not covered
    terminal = derive_run_terminal_state(goal, results)
    assert terminal == RUN_TERMINAL_FAILED


def test_empty_results_yields_failed_fail_closed() -> None:
    goal = _goal()
    terminal = derive_run_terminal_state(goal, [])
    assert terminal == RUN_TERMINAL_FAILED


def test_failing_verifier_yields_failed() -> None:
    goal = _goal()
    results = [_failing_result("c0"), _failing_result("d0")]
    terminal = derive_run_terminal_state(goal, results)
    assert terminal == RUN_TERMINAL_FAILED


def test_deterministic_across_calls() -> None:
    goal = _goal()
    results = [_passing_result("c0"), _passing_result("d0")]
    first = derive_run_terminal_state(goal, results)
    second = derive_run_terminal_state(goal, results)
    assert first == second


def test_every_failure_kind_resolves_to_a_terminal_state() -> None:
    goal = _goal()
    kinds = list(get_args(FailureConditionKind))
    assert set(kinds) == {
        "deliverable_missing",
        "success_criterion_unmet",
        "budget_exhausted",
        "blocker_unresolved",
        "capability_boundary",
        "operator_cancelled",
    }
    for kind in kinds:
        terminal = derive_run_terminal_state(
            goal,
            [],
            fired_failure_conditions=[_condition(kind)],
        )
        assert terminal in {
            RUN_TERMINAL_COMPLETED,
            RUN_TERMINAL_FAILED,
            RUN_TERMINAL_BLOCKED,
            RUN_TERMINAL_NEEDS_HUMAN,
            RUN_TERMINAL_BUDGET_EXHAUSTED,
        }


def test_kind_to_terminal_mapping_is_canonical() -> None:
    goal = _goal()
    mapping = {
        "deliverable_missing": RUN_TERMINAL_FAILED,
        "success_criterion_unmet": RUN_TERMINAL_FAILED,
        "budget_exhausted": RUN_TERMINAL_BUDGET_EXHAUSTED,
        "blocker_unresolved": RUN_TERMINAL_BLOCKED,
        "capability_boundary": RUN_TERMINAL_NEEDS_HUMAN,
        "operator_cancelled": RUN_TERMINAL_FAILED,
    }
    for kind, expected in mapping.items():
        terminal = derive_run_terminal_state(
            goal,
            [_passing_result("c0"), _passing_result("d0")],
            fired_failure_conditions=[_condition(kind)],
        )
        assert terminal == expected


def test_first_fired_condition_wins() -> None:
    goal = _goal()
    terminal = derive_run_terminal_state(
        goal,
        [_passing_result("c0"), _passing_result("d0")],
        fired_failure_conditions=[
            _condition("blocker_unresolved"),
            _condition("budget_exhausted"),
        ],
    )
    assert terminal == RUN_TERMINAL_BLOCKED


def test_completed_path_emits_run_completed_with_typed_provenance(
    session_env,
) -> None:
    sessions, session = session_env
    goal = _goal()
    run = _run(session.id)
    results = [_passing_result("c0"), _passing_result("d0")]

    event = bind_run_terminal_event(
        run=run,
        goal=goal,
        verifier_results=results,
        sessions=sessions,
        checkpoint_id="cp1",
    )

    assert event.event_type == f"run.{RUN_STATE_COMPLETED}"
    assert event.payload["terminal_state"] == RUN_TERMINAL_COMPLETED
    assert (
        event.payload[TERMINAL_STATE_PROVENANCE_FIELD]
        == TERMINAL_STATE_PROVENANCE_TYPED
    )
    assert event.payload["verifier_result_count"] == 2
    assert event.payload["verifier_pass_count"] == 2
    assert event.payload["verifier_fail_count"] == 0
    assert event.payload["goal_id"] == "g1"


def test_failed_path_emits_run_failed_with_typed_terminal_payload(session_env) -> None:
    sessions, session = session_env
    goal = _goal()
    run = _run(session.id)
    results = [_failing_result("c0"), _failing_result("d0")]

    event = bind_run_terminal_event(
        run=run,
        goal=goal,
        verifier_results=results,
        sessions=sessions,
        checkpoint_id="cp1",
    )
    assert event.event_type == f"run.{RUN_STATE_FAILED}"
    assert event.payload["terminal_state"] == RUN_TERMINAL_FAILED
    assert event.payload["verifier_pass_count"] == 0
    assert event.payload["verifier_fail_count"] == 2


def test_failure_condition_overrides_verifier_completion(session_env) -> None:
    sessions, session = session_env
    goal = _goal()
    run = _run(session.id)
    results = [_passing_result("c0"), _passing_result("d0")]
    event = bind_run_terminal_event(
        run=run,
        goal=goal,
        verifier_results=results,
        sessions=sessions,
        fired_failure_conditions=[_condition("budget_exhausted")],
        checkpoint_id="cp1",
    )
    assert event.event_type == f"run.{RUN_STATE_FAILED}"
    assert event.payload["terminal_state"] == RUN_TERMINAL_BUDGET_EXHAUSTED
    assert event.payload["fired_failure_condition_ids"] == ["f-budget_exhausted"]


def test_checkpoint_event_persisted_before_run_state_event(session_env) -> None:
    sessions, session = session_env
    goal = _goal()
    run = _run(session.id)
    results = [_passing_result("c0"), _passing_result("d0")]

    bind_run_terminal_event(
        run=run,
        goal=goal,
        verifier_results=results,
        sessions=sessions,
        checkpoint_id="cp1",
    )

    events = sessions.list_events(
        session_id=session.id,
        limit=50,
        newest_first=False,
    )
    types = [e.event_type for e in events]
    assert RUN_CHECKPOINT_EVENT_TYPE in types
    assert f"run.{RUN_STATE_COMPLETED}" in types
    cp_idx = types.index(RUN_CHECKPOINT_EVENT_TYPE)
    run_idx = types.index(f"run.{RUN_STATE_COMPLETED}")
    assert cp_idx < run_idx

    snapshot = events[cp_idx].payload["state_snapshot"]
    assert snapshot["terminal_state"] == RUN_TERMINAL_COMPLETED
    assert snapshot[TERMINAL_STATE_PROVENANCE_FIELD] == TERMINAL_STATE_PROVENANCE_TYPED
    assert len(snapshot["verifier_results"]) == 2


def test_run_goal_id_mismatch_rejected(session_env) -> None:
    sessions, session = session_env
    goal = _goal()
    run = _run(session.id, goal_id="g-mismatch")
    with pytest.raises(ValueError):
        bind_run_terminal_event(
            run=run,
            goal=goal,
            verifier_results=[],
            sessions=sessions,
            checkpoint_id="cp1",
        )


def test_missing_run_id_rejected_by_bind(session_env) -> None:
    sessions, session = session_env
    goal = _goal()
    run = Run(
        run_id="",
        session_id=session.id,
        goal_id="g1",
        state="running",
    )
    with pytest.raises(ValueError):
        bind_run_terminal_event(
            run=run,
            goal=goal,
            verifier_results=[],
            sessions=sessions,
            checkpoint_id="cp1",
        )


def test_extra_payload_does_not_overwrite_canonical_fields(session_env) -> None:
    sessions, session = session_env
    goal = _goal()
    run = _run(session.id)
    results = [_passing_result("c0"), _passing_result("d0")]
    event = bind_run_terminal_event(
        run=run,
        goal=goal,
        verifier_results=results,
        sessions=sessions,
        checkpoint_id="cp1",
        extra_payload={
            "channel": "console",
            "terminal_state": "this-should-not-overwrite",
        },
    )
    assert event.payload["terminal_state"] == RUN_TERMINAL_COMPLETED
    assert event.payload["channel"] == "console"


def test_derive_has_no_session_dependency() -> None:
    goal = _goal()
    terminal = derive_run_terminal_state(
        goal,
        [_passing_result("c0"), _passing_result("d0")],
    )
    assert isinstance(terminal, str)
    assert terminal == RUN_TERMINAL_COMPLETED
