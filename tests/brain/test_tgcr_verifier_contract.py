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
    Goal,
    SuccessCriterion,
    VerificationMode,
    WorkingState,
)
from openminion.modules.brain.schemas.commands import ToolCommand


class _StubSessionApi:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def append_event(
        self, session_id: str, event_type: str, payload: dict[str, Any], **_: Any
    ) -> str:
        self.events.append((event_type, payload))
        return f"event-{len(self.events)}"


def _state() -> WorkingState:
    return WorkingState(
        session_id="s1",
        agent_id="a1",
        budgets_remaining={
            "ticks": 1,
            "tool_calls": 1,
            "a2a_calls": 0,
            "tokens": 100,
            "time_ms": 1000,
        },
        trace_id="trace-1",
    )


def _logger() -> CanonicalEventLogger:
    return CanonicalEventLogger(
        session_api=_StubSessionApi(),
        session_id="s1",
        agent_id="a1",
    )


def _command_with_success_criteria(criteria: dict[str, Any]) -> ToolCommand:
    return ToolCommand(
        kind="tool",
        title="probe",
        tool_name="probe",
        success_criteria=criteria,
    )


def _action_result(
    *,
    command_id: str,
    status: str = "success",
    outputs: dict[str, Any] | None = None,
    artifact_refs: list[ArtifactRef] | None = None,
) -> ActionResult:
    return ActionResult(
        command_id=command_id,
        status=status,  # type: ignore[arg-type]
        outputs=outputs or {},
        artifact_refs=artifact_refs or [],
    )


class TestVerifierInvocationShape:
    def test_construction_with_criterion(self) -> None:
        cmd = _command_with_success_criteria({"ok": True})
        inv = VerifierInvocation(
            family="structural",
            goal_id="g1",
            run_id="r1",
            command=cmd,
            action_result=_action_result(command_id=cmd.command_id),
            criterion=SuccessCriterion(
                criterion_id="c1",
                description="x",
                structural_check="artifact_present",
            ),
        )
        assert inv.criterion is not None
        assert inv.deliverable is None

    def test_construction_with_deliverable(self) -> None:
        cmd = _command_with_success_criteria({})
        inv = VerifierInvocation(
            family="artifact_presence",
            goal_id="g1",
            run_id="r1",
            command=cmd,
            action_result=_action_result(
                command_id=cmd.command_id,
                artifact_refs=[ArtifactRef(ref="artifact://x")],
            ),
            deliverable=Deliverable(deliverable_id="d1", description="x"),
        )
        assert inv.deliverable is not None
        assert inv.criterion is None

    def test_requires_exactly_one_target(self) -> None:
        cmd = _command_with_success_criteria({})
        with pytest.raises(ValueError, match="exactly one"):
            VerifierInvocation(
                family="structural",
                goal_id="g1",
                run_id="r1",
                command=cmd,
                action_result=_action_result(command_id=cmd.command_id),
            )
        with pytest.raises(ValueError, match="exactly one"):
            VerifierInvocation(
                family="structural",
                goal_id="g1",
                run_id="r1",
                command=cmd,
                action_result=_action_result(command_id=cmd.command_id),
                criterion=SuccessCriterion(
                    criterion_id="c1",
                    description="x",
                    structural_check="artifact_present",
                ),
                deliverable=Deliverable(deliverable_id="d1", description="x"),
            )

    def test_requires_goal_id_and_run_id(self) -> None:
        cmd = _command_with_success_criteria({})
        crit = SuccessCriterion(
            criterion_id="c1", description="x", structural_check="artifact_present"
        )
        with pytest.raises(ValueError, match="goal_id"):
            VerifierInvocation(
                family="structural",
                goal_id="",
                run_id="r1",
                command=cmd,
                action_result=_action_result(command_id=cmd.command_id),
                criterion=crit,
            )
        with pytest.raises(ValueError, match="run_id"):
            VerifierInvocation(
                family="structural",
                goal_id="g1",
                run_id="",
                command=cmd,
                action_result=_action_result(command_id=cmd.command_id),
                criterion=crit,
            )


class TestRunVerifierDispatch:
    def test_artifact_presence_pass(self) -> None:
        cmd = _command_with_success_criteria({})
        inv = VerifierInvocation(
            family="artifact_presence",
            goal_id="g1",
            run_id="r1",
            command=cmd,
            action_result=_action_result(
                command_id=cmd.command_id,
                artifact_refs=[ArtifactRef(ref="artifact://x")],
            ),
            deliverable=Deliverable(deliverable_id="d1", description="x"),
        )
        result = run_verifier(inv, state=_state(), logger=_logger())
        assert result.passed is True
        assert result.verdict == "pass"
        assert result.target_id == "d1"
        assert result.reasons == []

    def test_artifact_presence_fail(self) -> None:
        cmd = _command_with_success_criteria({})
        inv = VerifierInvocation(
            family="artifact_presence",
            goal_id="g1",
            run_id="r1",
            command=cmd,
            action_result=_action_result(command_id=cmd.command_id),
            deliverable=Deliverable(deliverable_id="d1", description="x"),
        )
        result = run_verifier(inv, state=_state(), logger=_logger())
        assert result.passed is False
        assert result.verdict == "fail"
        assert any("Missing artifact_refs" in r for r in result.reasons)

    def test_structural_pass_when_criteria_met(self) -> None:
        cmd = _command_with_success_criteria({"ok": True})
        inv = VerifierInvocation(
            family="structural",
            goal_id="g1",
            run_id="r1",
            command=cmd,
            action_result=_action_result(
                command_id=cmd.command_id, outputs={"ok": True}
            ),
            criterion=SuccessCriterion(
                criterion_id="c1",
                description="x",
                structural_check="success_criteria.ok=true",
            ),
            mode=VerificationMode.rule_based,
        )
        result = run_verifier(inv, state=_state(), logger=_logger())
        assert result.passed is True

    def test_success_criteria_match_fail_when_outputs_missing(self) -> None:
        cmd = _command_with_success_criteria({"ok": True})
        inv = VerifierInvocation(
            family="success_criteria_match",
            goal_id="g1",
            run_id="r1",
            command=cmd,
            action_result=_action_result(
                command_id=cmd.command_id, outputs={"ok": False}
            ),
            criterion=SuccessCriterion(
                criterion_id="c1",
                description="x",
                structural_check="success_criteria.ok=true",
            ),
        )
        result = run_verifier(inv, state=_state(), logger=_logger())
        assert result.passed is False
        assert any("Structural" in r for r in result.reasons)

    def test_freshness_without_contract_fails_closed(self) -> None:
        cmd = _command_with_success_criteria({})
        inv = VerifierInvocation(
            family="freshness",
            goal_id="g1",
            run_id="r1",
            command=cmd,
            action_result=_action_result(command_id=cmd.command_id),
            criterion=SuccessCriterion(
                criterion_id="c1",
                description="x",
                structural_check="freshness",
            ),
        )
        result = run_verifier(inv, state=_state(), logger=_logger())
        assert result.passed is False
        assert any("freshness contract" in r for r in result.reasons)


class TestIsRunCompletionConfirmed:
    def _goal(self) -> Goal:
        return Goal(
            goal_id="g1",
            description="x",
            success_criteria=[
                SuccessCriterion(
                    criterion_id="c1",
                    description="x",
                    structural_check="artifact_present",
                )
            ],
            deliverables=[
                Deliverable(deliverable_id="d1", description="x"),
            ],
        )

    def _pass(self, target_id: str, family: str = "structural") -> VerifierResult:
        return VerifierResult(
            family=family,  # type: ignore[arg-type]
            goal_id="g1",
            run_id="r1",
            target_id=target_id,
            passed=True,
        )

    def _fail(self, target_id: str) -> VerifierResult:
        return VerifierResult(
            family="structural",
            goal_id="g1",
            run_id="r1",
            target_id=target_id,
            passed=False,
            reasons=["fail"],
        )

    def test_all_pass_confirms(self) -> None:
        results = [self._pass("c1"), self._pass("d1", "artifact_presence")]
        assert is_run_completion_confirmed(goal=self._goal(), results=results) is True

    def test_success_only_does_not_confirm(self) -> None:
        # Non-conflation: success criterion met but deliverable missing →
        # not completion-confirmed.
        results = [self._pass("c1")]
        assert is_run_completion_confirmed(goal=self._goal(), results=results) is False

    def test_deliverable_only_does_not_confirm(self) -> None:
        # Mirror of the above: deliverable produced but success criterion
        # unverified → not completion-confirmed.
        results = [self._pass("d1", "artifact_presence")]
        assert is_run_completion_confirmed(goal=self._goal(), results=results) is False

    def test_explicit_fail_blocks_confirmation(self) -> None:
        results = [self._pass("c1"), self._fail("d1")]
        assert is_run_completion_confirmed(goal=self._goal(), results=results) is False

    def test_no_results_does_not_confirm(self) -> None:
        # Pinned-rule enforcement: a model assertion with zero verifier
        # results MUST NOT be treated as completion.
        assert is_run_completion_confirmed(goal=self._goal(), results=[]) is False
