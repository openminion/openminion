from __future__ import annotations

from typing import Any

import pytest

from openminion.modules.brain.diagnostics.events import CanonicalEventLogger
from openminion.modules.brain.runtime.verification.policy import (
    VerifierInvocation,
    VerifierResult,
    is_run_completion_confirmed,
    run_verifier,
)
from openminion.modules.brain.schemas import (
    ActionResult,
    ArtifactRef,
    Deliverable,
    FailureCondition,
    Goal,
    SuccessCriterion,
    WorkingState,
)
from openminion.modules.brain.schemas.commands import ToolCommand
from openminion.services.runtime.run_status import (
    RUN_STATE_COMPLETED,
    RUN_STATE_FAILED,
    RUN_STATE_RUNNING,
    RUN_TERMINAL_COMPLETED,
    RUN_TERMINAL_FAILED,
    Run,
    resolve_run_terminal_persistence,
)


class _StubSessionApi:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def append_event(
        self, session_id: str, event_type: str, payload: dict[str, Any], **_: Any
    ) -> str:
        self.events.append((event_type, payload))
        return f"event-{len(self.events)}"


@pytest.fixture()
def working_state() -> WorkingState:
    return WorkingState(
        session_id="s-tgcr",
        agent_id="a-tgcr",
        budgets_remaining={
            "ticks": 5,
            "tool_calls": 5,
            "a2a_calls": 0,
            "tokens": 1000,
            "time_ms": 10000,
        },
        trace_id="trace-tgcr",
    )


@pytest.fixture()
def logger() -> CanonicalEventLogger:
    return CanonicalEventLogger(
        session_api=_StubSessionApi(),
        session_id="s-tgcr",
        agent_id="a-tgcr",
    )


def _build_goal() -> Goal:
    return Goal(
        goal_id="goal-deploy-report",
        description="Produce a deployment-health report",
        success_criteria=[
            SuccessCriterion(
                criterion_id="c-report-ok",
                description="Report fields validate",
                structural_check="success_criteria.ok=true",
            ),
        ],
        deliverables=[
            Deliverable(
                deliverable_id="d-report-artifact",
                description="Persisted artifact ref for the report",
                verification_hint="artifact_presence",
            ),
        ],
        failure_conditions=[
            FailureCondition(
                condition_id="f-deliverable-missing",
                kind="deliverable_missing",
                description="Report artifact was not produced",
            ),
        ],
        apd_plan_id="plan-deploy-report-v1",
    )


def _derive_run_terminal_from_results(
    *,
    goal: Goal,
    results: list[VerifierResult],
) -> str:

    if is_run_completion_confirmed(goal=goal, results=results):
        return RUN_TERMINAL_COMPLETED
    return RUN_TERMINAL_FAILED


def test_success_path(
    working_state: WorkingState, logger: CanonicalEventLogger
) -> None:
    goal = _build_goal()
    run = Run(
        run_id="run-001",
        session_id="s-tgcr",
        goal_id=goal.goal_id,
        state=RUN_STATE_RUNNING,
        apd_plan_id=goal.apd_plan_id,
    )

    # Structural execution produces a typed ActionResult that satisfies
    # both the success criterion (outputs.ok == True) and the deliverable
    # (artifact_refs non-empty).
    cmd = ToolCommand(
        kind="tool",
        title="produce_report",
        tool_name="produce_report",
        success_criteria={"ok": True},
    )
    action_result = ActionResult(
        command_id=cmd.command_id,
        status="success",
        outputs={"ok": True},
        artifact_refs=[ArtifactRef(ref="artifact://reports/deploy-2026-05-14")],
    )

    results = [
        run_verifier(
            VerifierInvocation(
                family="success_criteria_match",
                goal_id=goal.goal_id,
                run_id=run.run_id,
                command=cmd,
                action_result=action_result,
                criterion=goal.success_criteria[0],
            ),
            state=working_state,
            logger=logger,
        ),
        run_verifier(
            VerifierInvocation(
                family="artifact_presence",
                goal_id=goal.goal_id,
                run_id=run.run_id,
                command=cmd,
                action_result=action_result,
                deliverable=goal.deliverables[0],
            ),
            state=working_state,
            logger=logger,
        ),
    ]
    assert all(r.passed for r in results)
    assert is_run_completion_confirmed(goal=goal, results=results) is True

    terminal = _derive_run_terminal_from_results(goal=goal, results=results)
    assert terminal == RUN_TERMINAL_COMPLETED
    assert resolve_run_terminal_persistence(terminal) == RUN_STATE_COMPLETED


def test_deliverable_failure_path(
    working_state: WorkingState, logger: CanonicalEventLogger
) -> None:

    goal = _build_goal()
    run = Run(
        run_id="run-002",
        session_id="s-tgcr",
        goal_id=goal.goal_id,
        state=RUN_STATE_RUNNING,
    )

    cmd = ToolCommand(
        kind="tool",
        title="produce_report",
        tool_name="produce_report",
        success_criteria={"ok": True},
    )
    # Outputs satisfy the success criterion but no artifact was produced.
    action_result = ActionResult(
        command_id=cmd.command_id,
        status="success",
        outputs={"ok": True},
        artifact_refs=[],
    )

    success_result = run_verifier(
        VerifierInvocation(
            family="success_criteria_match",
            goal_id=goal.goal_id,
            run_id=run.run_id,
            command=cmd,
            action_result=action_result,
            criterion=goal.success_criteria[0],
        ),
        state=working_state,
        logger=logger,
    )
    deliverable_result = run_verifier(
        VerifierInvocation(
            family="artifact_presence",
            goal_id=goal.goal_id,
            run_id=run.run_id,
            command=cmd,
            action_result=action_result,
            deliverable=goal.deliverables[0],
        ),
        state=working_state,
        logger=logger,
    )

    assert success_result.passed is True
    assert deliverable_result.passed is False
    assert (
        is_run_completion_confirmed(
            goal=goal, results=[success_result, deliverable_result]
        )
        is False
    )

    terminal = _derive_run_terminal_from_results(
        goal=goal, results=[success_result, deliverable_result]
    )
    assert terminal == RUN_TERMINAL_FAILED
    assert resolve_run_terminal_persistence(terminal) == RUN_STATE_FAILED


def test_verifier_disagrees_path(
    working_state: WorkingState, logger: CanonicalEventLogger
) -> None:

    goal = _build_goal()
    run = Run(
        run_id="run-003",
        session_id="s-tgcr",
        goal_id=goal.goal_id,
        state=RUN_STATE_RUNNING,
    )

    cmd = ToolCommand(
        kind="tool",
        title="produce_report",
        tool_name="produce_report",
        success_criteria={"ok": True},
    )
    # The action returned a "successful" status but the structured
    # outputs DO NOT match the success criterion. A model-only judge
    # would call this complete; the structural verifier does not.
    action_result = ActionResult(
        command_id=cmd.command_id,
        status="success",
        outputs={"ok": False},
        artifact_refs=[ArtifactRef(ref="artifact://reports/deploy-2026-05-14")],
    )

    results = [
        run_verifier(
            VerifierInvocation(
                family="success_criteria_match",
                goal_id=goal.goal_id,
                run_id=run.run_id,
                command=cmd,
                action_result=action_result,
                criterion=goal.success_criteria[0],
            ),
            state=working_state,
            logger=logger,
        ),
        run_verifier(
            VerifierInvocation(
                family="artifact_presence",
                goal_id=goal.goal_id,
                run_id=run.run_id,
                command=cmd,
                action_result=action_result,
                deliverable=goal.deliverables[0],
            ),
            state=working_state,
            logger=logger,
        ),
    ]

    # The deliverable verifier passes (artifact present) but the
    # success-criterion verifier fails. is_run_completion_confirmed
    # MUST refuse to confirm the run as complete.
    assert any(r.passed for r in results)
    assert any(not r.passed for r in results)
    assert is_run_completion_confirmed(goal=goal, results=results) is False

    terminal = _derive_run_terminal_from_results(goal=goal, results=results)
    assert terminal == RUN_TERMINAL_FAILED
    assert resolve_run_terminal_persistence(terminal) == RUN_STATE_FAILED
