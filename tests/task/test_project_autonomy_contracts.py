from __future__ import annotations

import pytest

from openminion.modules.task import (
    AutonomyRunPhase,
    AutonomyRunStatus,
    TaskLifecycleRecord,
    TaskLifecycleState,
    build_autonomy_run,
    build_project_run_projection,
)
from openminion.modules.task.project import (
    AutonomyLoopConditionKind,
    AutonomyLoopJudgment,
    ProjectEffectRecord,
    ProjectEffectReplayDecision,
    ProjectEffectStatus,
    ProjectDomainVerificationContract,
    ProjectDomainVerificationEvidence,
    ProjectDomainVerificationStatus,
    ProjectOperatorInboxItem,
    ProjectOperatorResumeAction,
    ProjectOperatorWorkState,
    ProjectVerificationDomain,
    build_project_operator_inbox_item,
    classify_autonomy_loop_condition,
    evaluate_project_effect_replay,
    evaluate_project_verification_closure,
)
from openminion.modules.task.project.turn import (
    ProjectTurnRequest,
    project_turn_from_payload,
)


def _task_record(state: TaskLifecycleState = TaskLifecycleState.ACTIVE):
    return TaskLifecycleRecord(
        task_id="task-1",
        cron_job_id="cron-1",
        agent_id="agent-1",
        state=state,
        created_at="2026-07-29T00:00:00Z",
        updated_at="2026-07-29T00:00:01Z",
        cancelled_at=None,
        completed_at=None,
        failed_at=None,
        failure_reason=None,
    )


def _project_run(
    *,
    status: AutonomyRunStatus = AutonomyRunStatus.RUNNING,
    blocked_reason: str | None = None,
):
    run = build_autonomy_run(
        goal_text="ship durable autonomy",
        goal_id="goal-1",
        session_id="session-1",
        workspace_ref="local:/workspace#commit=abc;dirty=clean",
        max_iterations=3,
    ).model_copy(
        update={
            "task_id": "task-1",
            "checkpoint_id": "checkpoint-1",
            "status": status,
            "phase": AutonomyRunPhase.EXECUTE,
        }
    )
    return build_project_run_projection(
        run,
        objective_ledger_ref="artifact:objective.json",
        evidence_ledger_ref="artifact:evidence.jsonl",
        resume_packet_ref="artifact:resume.json",
        operator_decision_log_ref="artifact:operator-decisions.jsonl",
        capability_plan_ref="artifact:capabilities.json",
        metrics_summary_ref="artifact:metrics.json",
        blocked_reason=blocked_reason,
    )


@pytest.mark.parametrize(
    ("error_code", "summary"),
    (
        ("empty_provider_response", "provider response was empty"),
        ("unusable_provider_response", "provider response was unusable"),
        ("provider_timeout", "provider request timed out"),
        ("cancelled", "project turn was cancelled"),
        ("malformed_provider_response", "provider response was malformed"),
        ("context_overflow", "active context exceeded its budget"),
    ),
)
def test_project_turn_error_payloads_preserve_typed_error(
    error_code: str,
    summary: str,
) -> None:
    request = ProjectTurnRequest(
        run_id="run-1",
        project_run_id="project-1",
        task_id="task-1",
        goal_id="goal-1",
        session_id="session-1",
        cycle_id="cycle-1",
        milestone="milestone-1",
        prompt="continue",
    )

    result = project_turn_from_payload(
        request,
        payload={},
        execute=lambda _: {
            "error": True,
            "summary": summary,
            "metadata": {
                "error_code": error_code,
                "error_message": summary,
                "error_details": '{"request_id":"req-1"}',
            },
        },
    )

    assert result.error is not None
    assert result.error.code == error_code
    assert result.error.message == summary
    assert result.error.details == {"request_id": "req-1"}
    assert result.condition == (
        AutonomyLoopConditionKind.CANCELLED
        if error_code == "cancelled"
        else AutonomyLoopConditionKind.RETRYABLE_FAILURE
    )


def test_project_turn_decodes_typed_plan_metadata() -> None:
    request = ProjectTurnRequest(
        run_id="run-1",
        project_run_id="project-1",
        task_id="task-1",
        goal_id="goal-1",
        session_id="session-1",
        cycle_id="cycle-1",
        milestone="milestone-1",
        prompt="continue",
    )

    result = project_turn_from_payload(
        request,
        payload={},
        execute=lambda _: {
            "summary": "planned",
            "metadata": {
                "task_plan": (
                    '{"plan_id":"plan-1","objective":"ship",'
                    '"criterion_ids":["criterion-tests"],'
                    '"steps":[{"step_id":"build","description":"Build"}]}'
                ),
                "task_plan.revision": (
                    '{"plan_id":"plan-1","revision_id":"revision-1",'
                    '"criterion_ids":["criterion-tests"],'
                    '"verifier_refs":["verify:failed-1"],'
                    '"revised_steps":[{"step_id":"build",'
                    '"description":"Repair"}]}'
                ),
            },
        },
    )

    assert result.task_plan is not None
    assert result.task_plan.criterion_ids == ["criterion-tests"]
    assert result.task_plan_revision is not None
    assert result.task_plan_revision.revision_id == "revision-1"


@pytest.mark.parametrize(
    ("details", "expected"),
    (
        ("not-json", {"error": "malformed_details"}),
        ("[]", {"error": "non_object_details"}),
        ('{"api_key":"secret","status_code":429}', {"status_code": "429"}),
    ),
)
def test_project_turn_error_details_are_bounded(details: str, expected: dict) -> None:
    request = ProjectTurnRequest(
        run_id="run-1",
        project_run_id="project-1",
        task_id="task-1",
        goal_id="goal-1",
        session_id="session-1",
        cycle_id="cycle-1",
        milestone="milestone-1",
        prompt="continue",
    )

    result = project_turn_from_payload(
        request,
        payload={},
        execute=lambda _: {
            "error": True,
            "summary": "failed",
            "metadata": {
                "error_code": "provider_timeout",
                "error_details": details,
            },
        },
    )

    assert result.error is not None
    assert result.error.details == expected


def test_operator_inbox_projects_all_local_states_with_resume_actions() -> None:
    cases = [
        (
            AutonomyRunStatus.RUNNING,
            ProjectOperatorWorkState.RUNNING,
            ProjectOperatorResumeAction.CONTINUE,
            None,
        ),
        (
            AutonomyRunStatus.WAITING_FOR_APPROVAL,
            ProjectOperatorWorkState.WAITING,
            ProjectOperatorResumeAction.APPROVE,
            "approve policy request",
        ),
        (
            AutonomyRunStatus.WAITING_FOR_INPUT,
            ProjectOperatorWorkState.WAITING,
            ProjectOperatorResumeAction.ANSWER_INPUT,
            "answer missing detail",
        ),
        (
            AutonomyRunStatus.BLOCKED,
            ProjectOperatorWorkState.BLOCKED,
            ProjectOperatorResumeAction.INSPECT_BLOCKER,
            "install missing capability",
        ),
        (
            AutonomyRunStatus.COMPLETED,
            ProjectOperatorWorkState.COMPLETED,
            ProjectOperatorResumeAction.NONE,
            None,
        ),
        (
            AutonomyRunStatus.CANCELLED,
            ProjectOperatorWorkState.CANCELLED,
            ProjectOperatorResumeAction.NONE,
            None,
        ),
        (
            AutonomyRunStatus.FAILED,
            ProjectOperatorWorkState.FAILED,
            ProjectOperatorResumeAction.NONE,
            None,
        ),
    ]

    for status, state, resume_action, hint in cases:
        item = build_project_operator_inbox_item(
            _project_run(status=status, blocked_reason=hint),
            task_record=_task_record(),
            current_step_ref="plan:step-1",
            next_resume_action=hint,
            artifact_refs=("artifact:proof.json",),
        )

        assert item.state == state
        assert item.resume_action == resume_action
        assert item.current_step_ref == "plan:step-1"
        assert item.last_checkpoint_id == "checkpoint-1"
        assert item.artifact_refs == ("artifact:proof.json",)


def test_operator_inbox_rejects_waiting_without_resume_path() -> None:
    with pytest.raises(ValueError, match="resume_hint or blocker"):
        ProjectOperatorInboxItem(
            task_id="task-1",
            state=ProjectOperatorWorkState.WAITING,
            resume_action=ProjectOperatorResumeAction.APPROVE,
        )


def test_task_lifecycle_terminal_state_overrides_project_status() -> None:
    item = build_project_operator_inbox_item(
        _project_run(status=AutonomyRunStatus.RUNNING),
        task_record=_task_record(TaskLifecycleState.CANCELLED),
    )

    assert item.state == ProjectOperatorWorkState.CANCELLED


def test_domain_verification_closure_passes_only_with_required_structural_evidence() -> (
    None
):
    contract = ProjectDomainVerificationContract(
        domain=ProjectVerificationDomain.CODING,
        required_evidence_kinds=("diff", "focused_tests"),
        verifier_ref="coding-verifier",
    )

    verified = evaluate_project_verification_closure(
        contract,
        ProjectDomainVerificationEvidence(
            domain=ProjectVerificationDomain.CODING,
            evidence_kinds=("diff", "focused_tests"),
            evidence_refs=("artifact:diff.patch", "pytest:focused"),
        ),
    )
    partial = evaluate_project_verification_closure(
        contract,
        ProjectDomainVerificationEvidence(
            domain=ProjectVerificationDomain.CODING,
            evidence_kinds=("diff",),
            evidence_refs=("artifact:diff.patch",),
        ),
    )
    failed = evaluate_project_verification_closure(
        contract,
        ProjectDomainVerificationEvidence(
            domain=ProjectVerificationDomain.CODING,
            prose_only_completion=True,
        ),
    )

    assert verified.status == ProjectDomainVerificationStatus.VERIFIED
    assert partial.status == ProjectDomainVerificationStatus.PARTIAL
    assert partial.missing_evidence_kinds == ("focused_tests",)
    assert failed.status == ProjectDomainVerificationStatus.FAILED
    assert failed.reason == "malformed_or_prose_only_evidence"


def test_domain_verification_closure_reports_blocked_or_needs_user() -> None:
    contract = ProjectDomainVerificationContract(
        domain=ProjectVerificationDomain.RESEARCH,
        required_evidence_kinds=("claim_source_map",),
        verifier_ref="research-verifier",
    )

    blocked = evaluate_project_verification_closure(
        contract,
        ProjectDomainVerificationEvidence(
            domain=ProjectVerificationDomain.RESEARCH,
            unsupported_reason="search provider unavailable",
        ),
    )
    needs_user = evaluate_project_verification_closure(
        contract,
        ProjectDomainVerificationEvidence(
            domain=ProjectVerificationDomain.RESEARCH,
            needs_user_reason="scope conflict",
        ),
    )

    assert blocked.status == ProjectDomainVerificationStatus.BLOCKED
    assert needs_user.status == ProjectDomainVerificationStatus.NEEDS_USER


def test_domain_verification_rejects_mismatched_domain_and_unreferenced_evidence() -> (
    None
):
    contract = ProjectDomainVerificationContract(
        domain=ProjectVerificationDomain.OPERATIONS,
        required_evidence_kinds=("read_after_write",),
        verifier_ref="ops-verifier",
    )
    with pytest.raises(ValueError, match="domain"):
        evaluate_project_verification_closure(
            contract,
            ProjectDomainVerificationEvidence(
                domain=ProjectVerificationDomain.CROSS_APPLICATION,
            ),
        )
    with pytest.raises(ValueError, match="evidence_refs"):
        ProjectDomainVerificationEvidence(
            domain=ProjectVerificationDomain.OPERATIONS,
            evidence_kinds=("read_after_write",),
        )


def test_project_effect_record_requires_result_and_reversal_posture() -> None:
    effect = ProjectEffectRecord(
        effect_id="effect-1",
        task_id="task-1",
        idempotency_key="idem-1",
        actor_ref="task:task-1",
        capability_ref="tool:file.write",
        precondition_refs=("artifact:before.json",),
        approval_ref="policy:approval-1",
        result_ref="artifact:after.json",
        verification_refs=("verify:read-after-write",),
        rollback_ref="artifact:rollback.patch",
        status=ProjectEffectStatus.SUCCEEDED,
    )

    assert effect.status == ProjectEffectStatus.SUCCEEDED
    with pytest.raises(ValueError, match="rollback_ref or non_reversible_reason"):
        ProjectEffectRecord(
            effect_id="effect-2",
            task_id="task-1",
            idempotency_key="idem-2",
            actor_ref="task:task-1",
            capability_ref="tool:file.write",
            precondition_refs=("artifact:before.json",),
            result_ref="artifact:after.json",
            status=ProjectEffectStatus.SUCCEEDED,
        )


def test_project_effect_replay_reuses_completed_and_blocks_stale_precondition() -> None:
    existing = ProjectEffectRecord(
        effect_id="effect-1",
        task_id="task-1",
        idempotency_key="idem-1",
        actor_ref="task:task-1",
        capability_ref="tool:file.write",
        precondition_refs=("artifact:before.json",),
        result_ref="artifact:after.json",
        non_reversible_reason="external write cannot be rolled back automatically",
        status=ProjectEffectStatus.SUCCEEDED,
    )

    reuse = evaluate_project_effect_replay(
        existing,
        idempotency_key="idem-1",
        precondition_refs=("artifact:before.json",),
    )
    stale = evaluate_project_effect_replay(
        existing,
        idempotency_key="idem-1",
        precondition_refs=("artifact:changed-before.json",),
    )
    retry = evaluate_project_effect_replay(
        existing.model_copy(update={"status": ProjectEffectStatus.FAILED}),
        idempotency_key="idem-1",
        precondition_refs=("artifact:before.json",),
    )

    assert reuse.decision == ProjectEffectReplayDecision.REUSE_EXISTING
    assert reuse.allowed is False
    assert stale.decision == ProjectEffectReplayDecision.BLOCK_STALE_PRECONDITION
    assert retry.decision == ProjectEffectReplayDecision.ALLOW


def test_project_effect_replay_blocks_duplicate_in_progress_effect() -> None:
    existing = ProjectEffectRecord(
        effect_id="effect-1",
        task_id="task-1",
        idempotency_key="idem-1",
        actor_ref="task:task-1",
        capability_ref="tool:file.write",
        precondition_refs=("artifact:before.json",),
        status=ProjectEffectStatus.STARTED,
    )

    duplicate = evaluate_project_effect_replay(
        existing,
        idempotency_key="idem-1",
        precondition_refs=("artifact:before.json",),
    )

    assert duplicate.decision == ProjectEffectReplayDecision.BLOCK_DUPLICATE


def test_autonomy_loop_classification_covers_daep_no_progress_taxonomy() -> None:
    expected = {
        AutonomyLoopConditionKind.PRODUCTIVE: (AutonomyRunStatus.RUNNING, False, False),
        AutonomyLoopConditionKind.WAITING: (
            AutonomyRunStatus.WAITING_FOR_INPUT,
            False,
            True,
        ),
        AutonomyLoopConditionKind.RETRYABLE_FAILURE: (
            AutonomyRunStatus.RUNNING,
            False,
            False,
        ),
        AutonomyLoopConditionKind.MISSING_CAPABILITY: (
            AutonomyRunStatus.BLOCKED,
            False,
            True,
        ),
        AutonomyLoopConditionKind.DENIED: (AutonomyRunStatus.BLOCKED, False, True),
        AutonomyLoopConditionKind.DUPLICATE_ACTION: (
            AutonomyRunStatus.BLOCKED,
            True,
            False,
        ),
        AutonomyLoopConditionKind.BUDGET_EXHAUSTED: (
            AutonomyRunStatus.BLOCKED,
            False,
            True,
        ),
        AutonomyLoopConditionKind.DEADLINE_EXHAUSTED: (
            AutonomyRunStatus.BLOCKED,
            False,
            True,
        ),
        AutonomyLoopConditionKind.STRATEGY_FAILURE: (
            AutonomyRunStatus.RUNNING,
            True,
            False,
        ),
        AutonomyLoopConditionKind.TERMINAL_INABILITY: (
            AutonomyRunStatus.FAILED,
            False,
            False,
        ),
    }

    for condition, (status, replan, operator) in expected.items():
        judgment = classify_autonomy_loop_condition(
            condition=condition,
            evidence_refs=("event:typed-signal",),
        )

        assert judgment.run_status == status
        assert judgment.requires_model_replan is replan
        assert judgment.requires_operator is operator
        assert judgment.evidence_refs == ("event:typed-signal",)


def test_autonomy_loop_judgment_rejects_unknown_fields_and_missing_operator_action() -> (
    None
):
    with pytest.raises(ValueError):
        AutonomyLoopJudgment(
            condition=AutonomyLoopConditionKind.WAITING,
            run_status=AutonomyRunStatus.WAITING_FOR_INPUT,
            requires_operator=True,
            reason_code="waiting",
        )
    with pytest.raises(ValueError):
        AutonomyLoopJudgment(
            condition=AutonomyLoopConditionKind.PRODUCTIVE,
            run_status=AutonomyRunStatus.RUNNING,
            reason_code="progress",
            assistant_text="I think I am stuck",  # type: ignore[call-arg]
        )
