from __future__ import annotations

import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from openminion.modules.brain.loop.tools.messages import action_result_to_tool_message
from openminion.modules.brain.schemas import ActionError, ActionResult
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.security.plugin import _h_publish_report, _h_review_report
from openminion.tools.security.review_schemas import SecurityReviewArgs
from tests.tools.security.test_audit_report import (
    _publish_args,
    _scan,
    _store_scan,
    audit_context as audit_context,
)


def _source(ctx, revision, *, finding_ids=("F-1",), author="researcher"):
    ctx.agent_id = author
    ctx.trace_id = f"{author}-trace"
    ctx.tool_name = "security.publish_report"
    scan = _scan(check="code", revision=revision)
    evidence_ref = _store_scan(ctx, scan)
    findings = [
        {
            "finding_id": finding_id,
            "basis": "manual",
            "disposition": "candidate",
            "title": "Synthetic validation issue",
            "category": "validation",
            "severity": "medium",
            "confidence": "high",
            "description": "The fixture accepts an invalid value.",
            "locations": [{"path": "app.py", "line": 1}],
            "evidence_summary": "Observed the approved fixture's invalid-value case.",
        }
        for finding_id in finding_ids
    ]
    result = _h_publish_report(
        _publish_args(
            revision=revision, refs={"code": evidence_ref}, findings=findings
        ),
        ctx,
    )
    return result["report_ref"]


def _reviewer(ctx, **changes):
    return replace(
        ctx,
        agent_id="reviewer",
        session_id="review-session",
        trace_id="review-trace",
        tool_name="security.review_report",
        artifacts=[],
        **changes,
    )


def _decision(finding_id="F-1", decision="accepted"):
    item = {
        "finding_id": finding_id,
        "decision": decision,
        "rationale": "Independent fixture review.",
    }
    if decision == "accepted":
        item.update(
            remediation_objective="Reject invalid fixture input.",
            verification_criteria=[
                "The regression fails before and passes after repair."
            ],
        )
    elif decision == "needs_more_evidence":
        item["evidence_requests"] = ["Supply the approved reproduction."]
    return item


def _initial(ref, decisions=None):
    return {
        "review_kind": "initial",
        "report_ref": ref,
        "finding_reviews": decisions or [_decision()],
        "summary": "Independent source review.",
    }


def _followup(prior_ref, rescan_ref, **changes):
    return {
        "review_kind": "follow_up",
        "prior_review_ref": prior_ref,
        "rescan_report_ref": rescan_ref,
        "closure_reviews": [
            {
                "finding_id": "F-1",
                "outcome": "resolved",
                "rationale": "The complete rescan and regression support closure.",
            }
        ],
        "new_finding_reviews": [],
        "summary": "Reviewed the patched revision.",
        **changes,
    }


def _read(ctx, ref):
    return json.loads(ctx.artifactctl.read_bytes(ref))


def _rewrite(ctx, ref, update, **meta_changes):
    payload = _read(ctx, ref)
    update(payload)
    metadata = ctx.artifactctl.get(ref)
    return ctx.artifactctl.ingest_bytes(
        data=json.dumps(payload).encode(),
        mime="application/json",
        session_id=meta_changes.get("session_id", metadata.session_id),
        agent_id=meta_changes.get("agent_id", metadata.agent_id),
        trace_id=meta_changes.get("trace_id", metadata.trace_id),
        meta=meta_changes.get("meta", metadata.meta_json),
    ).ref


def test_initial_review_is_immutable_and_projects_only_accepted_findings(audit_context):
    ctx, revision = audit_context
    ref = _source(ctx, revision, finding_ids=("F-1", "F-2", "F-3"))
    original = ctx.artifactctl.read_bytes(ref)
    result = _h_review_report(
        _initial(
            ref,
            [
                _decision(),
                _decision("F-2", "rejected"),
                _decision("F-3", "needs_more_evidence"),
            ],
        ),
        _reviewer(ctx),
    )
    report = _read(ctx, result["review_ref"])
    assert report["review_status"] == "mixed"
    assert report["reviewer_agent_id"] == "reviewer"
    assert report["report_author_agent_id"] == "researcher"
    assert result["artifact_refs"] == [result["review_ref"]]
    assert result["accepted_count"] == 1
    assert [item["finding_id"] for item in report["remediation_brief"]] == ["F-1"]
    assert result["review_ref"] not in json.dumps(report)
    assert ctx.artifactctl.read_bytes(ref) == original
    assert _read(ctx, ref)["review_status"] == "unreviewed"


@pytest.mark.parametrize("decision", ["accepted", "rejected", "needs_more_evidence"])
def test_initial_status_is_derived(audit_context, decision):
    ctx, revision = audit_context
    ref = _source(ctx, revision)
    result = _h_review_report(
        _initial(ref, [_decision(decision=decision)]), _reviewer(ctx)
    )
    assert result["review_status"] == decision


@pytest.mark.parametrize("ids", [[], ["F-1", "F-1"], ["unknown"]])
def test_initial_requires_exact_nonempty_candidates(audit_context, ids):
    ctx, revision = audit_context
    ref = _source(ctx, revision)
    request = {**_initial(ref), "finding_reviews": [_decision(item) for item in ids]}
    with pytest.raises((ValidationError, ToolRuntimeError)):
        _h_review_report(request, _reviewer(ctx))


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(execution_status="partial"),
        lambda p: p["checks"][0].update(truncated=True),
        lambda p: p.update(checks=[]),
        lambda p: p.update(schema_version="security-audit-report/v2"),
        lambda p: p.update(findings=[]),
    ],
)
def test_review_refuses_incomplete_or_wrong_report(audit_context, change):
    ctx, revision = audit_context
    ref = _rewrite(ctx, _source(ctx, revision), change)
    reviewer = _reviewer(ctx)
    with pytest.raises(ToolRuntimeError):
        _h_review_report(_initial(ref), reviewer)
    assert reviewer.artifacts == []


@pytest.mark.parametrize(
    "metadata",
    [
        {"agent_id": None},
        {"session_id": None},
        {"trace_id": None},
        {"agent_id": "reviewer"},
        {"meta": {}},
        {
            "meta": {
                "runtime_bridge": "RuntimeContext.write_artifact",
                "tool_name": "file.write",
            }
        },
    ],
)
def test_review_requires_producer_identity_and_refuses_self_review(
    audit_context, metadata
):
    ctx, revision = audit_context
    ref = _rewrite(
        ctx,
        _source(ctx, revision),
        lambda p: p.update(summary="Distinct fixture bytes."),
        **metadata,
    )
    with pytest.raises(ToolRuntimeError):
        _h_review_report(_initial(ref), _reviewer(ctx))


@pytest.mark.parametrize(
    "field,value",
    [
        ("permission_mode", "ask"),
        ("agent_id", None),
        ("session_id", None),
        ("trace_id", ""),
        ("artifactctl", None),
    ],
)
def test_review_requires_readonly_runtime_and_identity(audit_context, field, value):
    ctx, revision = audit_context
    ref = _source(ctx, revision)
    reviewer = _reviewer(ctx)
    setattr(reviewer, field, value)
    with pytest.raises(ToolRuntimeError):
        _h_review_report(_initial(ref), reviewer)


def test_review_redacts_free_text_and_requires_canonical_publication(
    audit_context, monkeypatch
):
    ctx, revision = audit_context
    ref = _source(ctx, revision)
    secret = "sk-" + "a" * 36
    args = _initial(ref)
    args["finding_reviews"][0]["rationale"] = f"Credential {secret}"
    result = _h_review_report(args, _reviewer(ctx))
    assert secret not in ctx.artifactctl.read_bytes(result["review_ref"]).decode()
    assert _read(ctx, result["review_ref"])["redaction_count"] > 0
    monkeypatch.setattr(ctx.artifactctl, "ingest_bytes", lambda **kwargs: None)
    with pytest.raises(ToolRuntimeError, match="canonical"):
        _h_review_report(_initial(ref), _reviewer(ctx))


def test_followup_resolves_against_fresh_revision(audit_context):
    ctx, revision = audit_context
    source = _source(ctx, revision)
    prior = _h_review_report(_initial(source), _reviewer(ctx))
    rescan = _source(ctx, "b" * 40, finding_ids=())
    result = _h_review_report(_followup(prior["review_ref"], rescan), _reviewer(ctx))
    report = _read(ctx, result["review_ref"])
    assert result["review_status"] == "resolved"
    assert report["scope"]["target_revision"] == revision
    assert report["rescan_revision"] == "b" * 40
    assert report["source_report_ref"] == source
    assert report["prior_review_ref"] == prior["review_ref"]
    assert report["remediation_brief"] == []


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p["scope"].update(target="other"),
        lambda p: p.update(execution_status="partial"),
        lambda p: p["checks"][0].update(truncated=True),
        lambda p: p["checks"][0].update(scanner_version="different"),
        lambda p: p["checks"][0]["configuration_identity"].update(
            config_sha256="c" * 64
        ),
        lambda p: p["scope"].update(requested_checks=["secrets"]),
    ],
)
def test_followup_refuses_changed_or_incomplete_coverage(audit_context, change):
    ctx, revision = audit_context
    prior = _h_review_report(_initial(_source(ctx, revision)), _reviewer(ctx))
    rescan = _rewrite(ctx, _source(ctx, "b" * 40, finding_ids=()), change)
    with pytest.raises(ToolRuntimeError):
        _h_review_report(_followup(prior["review_ref"], rescan), _reviewer(ctx))


@pytest.mark.parametrize(
    "new_decision,status",
    [("accepted", "mixed"), ("needs_more_evidence", "mixed"), ("rejected", "resolved")],
)
def test_new_actionable_findings_prevent_aggregate_resolution(
    audit_context, new_decision, status
):
    ctx, revision = audit_context
    prior = _h_review_report(_initial(_source(ctx, revision)), _reviewer(ctx))
    rescan = _source(ctx, "b" * 40, finding_ids=("NEW",))
    args = _followup(
        prior["review_ref"],
        rescan,
        new_finding_reviews=[_decision("NEW", new_decision)],
    )
    result = _h_review_report(args, _reviewer(ctx))
    assert result["review_status"] == status
    assert result["accepted_count"] == int(new_decision == "accepted")


@pytest.mark.parametrize(
    "kind",
    [
        "same_revision",
        "no_accepted",
        "rescan_self",
        "original_self",
        "unknown_match",
        "missing_new",
        "duplicate_closure",
        "extra_closure",
    ],
)
def test_followup_admission_and_identity_negatives(audit_context, kind):
    ctx, revision = audit_context
    source = _source(ctx, revision, author="original")
    decision = "rejected" if kind == "no_accepted" else "accepted"
    prior = _h_review_report(
        _initial(source, [_decision(decision=decision)]), _reviewer(ctx)
    )
    rescan = _source(
        ctx,
        revision if kind == "same_revision" else "b" * 40,
        finding_ids=("NEW",) if kind == "missing_new" else (),
        author="reviewer" if kind == "rescan_self" else "researcher",
    )
    args = _followup(prior["review_ref"], rescan)
    reviewer = _reviewer(ctx)
    if kind == "original_self":
        reviewer.agent_id = "original"
    if kind == "unknown_match":
        args["closure_reviews"][0].update(
            outcome="residual",
            current_finding_ids=["unknown"],
            next_step_guidance="Investigate.",
        )
    if kind == "duplicate_closure":
        args["closure_reviews"] *= 2
    if kind == "extra_closure":
        args["closure_reviews"].append(
            {**args["closure_reviews"][0], "finding_id": "EXTRA"}
        )
    before = list(reviewer.artifacts)
    with pytest.raises(ToolRuntimeError) as failure:
        _h_review_report(args, reviewer)
    assert reviewer.artifacts == before
    if kind in {"duplicate_closure", "extra_closure", "missing_new"}:
        expected = ["NEW"] if kind == "missing_new" else ["F-1"]
        received = {
            "duplicate_closure": ["F-1", "F-1"],
            "extra_closure": ["F-1", "EXTRA"],
            "missing_new": [],
        }[kind]
        feedback = action_result_to_tool_message(
            "review-failure",
            "security.review_report",
            ActionResult(
                command_id="review-failure",
                status="failed",
                error=ActionError(
                    code=failure.value.code,
                    message=failure.value.message,
                    details=failure.value.details,
                ),
            ),
        )
        assert json.loads(feedback.content)["error"]["details"] == {
            "expected_finding_ids": expected,
            "received_finding_ids": received,
        }


@pytest.mark.parametrize(
    "decision",
    [
        {"decision": "accepted"},
        {"decision": "rejected", "remediation_objective": "Forbidden"},
        {"decision": "needs_more_evidence"},
    ],
)
def test_decision_specific_schema_rejects_invalid_fields(decision):
    args = _initial("artifact://sha256/" + "a" * 64)
    args["finding_reviews"] = [
        {"finding_id": "F-1", "rationale": "Evidence.", **decision}
    ]
    with pytest.raises(ValidationError) as failure:
        SecurityReviewArgs.model_validate(args)
    feedback = action_result_to_tool_message(
        "review-failure",
        "security.review_report",
        ActionResult(
            command_id="review-failure",
            status="failed",
            error=ActionError(code="INVALID_ARGUMENT", message=str(failure.value)),
        ),
    )
    message = json.loads(feedback.content)["error"]["message"]
    assert (
        "accepted requires remediation_objective and verification_criteria; omit evidence_requests"
        in message
    )
    assert (
        "needs_more_evidence requires evidence_requests; omit remediation_objective and verification_criteria"
        in message
    )
    assert "rejected must omit all three fields" in message
    args["finding_reviews"] = [_decision(decision=decision["decision"])]
    assert SecurityReviewArgs.model_validate(args).root.finding_reviews


@pytest.mark.parametrize("outcome", ["residual", "not_reproduced"])
def test_followup_retains_nonresolved_guidance(audit_context, outcome):
    ctx, revision = audit_context
    prior = _h_review_report(_initial(_source(ctx, revision)), _reviewer(ctx))
    current_ids = ("CURRENT",) if outcome == "residual" else ()
    rescan = _source(ctx, "b" * 40, finding_ids=current_ids)
    args = _followup(prior["review_ref"], rescan)
    args["closure_reviews"][0].update(
        outcome=outcome,
        current_finding_ids=list(current_ids),
        next_step_guidance="Gather the missing evidence before a new repair decision.",
    )
    result = _h_review_report(args, _reviewer(ctx))
    assert result["review_status"] == outcome
    assert _read(ctx, result["review_ref"])["closure_reviews"][0]["next_step_guidance"]


@pytest.mark.parametrize("which", ["source", "prior", "rescan", "scan"])
def test_deleted_evidence_cannot_close_findings(audit_context, which):
    ctx, revision = audit_context
    source = _source(ctx, revision)
    prior = _h_review_report(_initial(source), _reviewer(ctx))["review_ref"]
    rescan = _source(ctx, "b" * 40, finding_ids=())
    ref = {
        "source": source,
        "prior": prior,
        "rescan": rescan,
        "scan": _read(ctx, source)["evidence_refs"][0],
    }[which]
    ctx.artifactctl.delete(ref)
    with pytest.raises(ToolRuntimeError):
        _h_review_report(_followup(prior, rescan), _reviewer(ctx))


def test_redaction_preserves_structural_runtime_identity(audit_context):
    ctx, revision = audit_context
    source = _source(ctx, revision)
    reviewer = _reviewer(ctx)
    reviewer.agent_id = "sk-" + "b" * 36
    result = _h_review_report(_initial(source), reviewer)
    assert _read(ctx, result["review_ref"])["reviewer_agent_id"] == reviewer.agent_id


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(summary="x" * 4001),
        lambda p: p.update(extra="not a contract field"),
        lambda p: p.update(finding_reviews=[_decision()] * 201),
        lambda p: p["finding_reviews"][0].update(verification_criteria=[]),
        lambda p: p["finding_reviews"][0].update(evidence_requests=["not allowed"]),
        lambda p: p["finding_reviews"][0].update(rationale="x" * 3001),
    ],
)
def test_review_input_bounds(change):
    args = _initial("artifact://sha256/" + "a" * 64)
    change(args)
    with pytest.raises(ValidationError):
        SecurityReviewArgs.model_validate(args)


def test_redaction_expansion_returns_typed_error_without_publication(audit_context):
    ctx, revision = audit_context
    args = _initial(_source(ctx, revision))
    args["finding_reviews"][0]["rationale"] = "x" * 2992 + " token=a"
    reviewer = _reviewer(ctx)
    with pytest.raises(ToolRuntimeError, match="shorten the prose") as failure:
        _h_review_report(args, reviewer)
    assert failure.value.code == "INVALID_ARGUMENT"
    assert reviewer.artifacts == []


@pytest.fixture
def linked_followup(audit_context, tmp_path):
    from tests.task.test_lifecycle import _manager
    from tests.tools.security.test_schedule import admission

    ctx, revision = audit_context
    source = _source(ctx, revision)
    prior = _h_review_report(_initial(source), _reviewer(ctx))["review_ref"]
    rescan = _source(ctx, "b" * 40, finding_ids=())
    manager = _manager(tmp_path)
    scheduled = manager.schedule_user_task(
        name="source scan",
        instruction="scan",
        agent_id="researcher",
        schedule={"kind": "every", "every_ms": 60000},
        security_scan=admission(ctx.workspace).model_dump(mode="json"),
    )
    task_id = scheduled["record"].task_id
    manager.record_scheduled_outcome(
        cron_job_id=scheduled["job"]["job_id"],
        run={
            "run_id": "rescan-run",
            "state": "finished",
            "attempts": 1,
            "output": {
                "security_scan": {
                    "status": "completed",
                    "job_id": scheduled["job"]["job_id"],
                    "run_id": "rescan-run",
                    "report_ref": rescan,
                    "revision": "b" * 40,
                }
            },
        },
    )
    manager.mutate_task_metadata(
        task_id=task_id,
        mutate=lambda metadata: {
            **metadata,
            "security_repair": {
                "run_id": "repair-run",
                "review_ref": prior,
                "finding_id": "F-1",
            },
        },
    )
    try:
        yield (
            _reviewer(ctx, task_manager=manager),
            _followup(prior, rescan, source_task_id=task_id),
        )
    finally:
        manager.close()


def test_followup_links_exact_source_and_preserves_prior_review(linked_followup):
    ctx, args = linked_followup
    prior = ctx.artifactctl.read_bytes(args["prior_review_ref"])
    result = _h_review_report(args, ctx)
    record = ctx.task_manager.get_task(args["source_task_id"])
    assert record.metadata["security_repair"] == {
        "run_id": "repair-run",
        "review_ref": args["prior_review_ref"],
        "finding_id": "F-1",
        "follow_up_review_ref": result["review_ref"],
        "rescan_report_ref": args["rescan_report_ref"],
        "finding_outcome": "resolved",
    }
    assert ctx.artifactctl.read_bytes(args["prior_review_ref"]) == prior
    assert (
        result["review_ref"].rsplit("/", 1)[1]
        in ctx.artifactctl.index.active_reference_shas()
    )


@pytest.mark.parametrize(
    "invalid",
    [
        "missing_task",
        "missing_manager",
        "wrong_review",
        "wrong_rescan",
        "failed_latest",
        "stale_completed_run",
        "wrong_finding",
    ],
)
def test_followup_refuses_mismatched_source_without_publishing(
    linked_followup, invalid
):
    ctx, args = linked_followup
    manager = ctx.task_manager
    task_id = args["source_task_id"]
    metadata = manager.get_task(task_id).metadata
    if invalid == "missing_task":
        args["source_task_id"] = "missing-task"
    elif invalid == "missing_manager":
        ctx.task_manager = None
    elif invalid == "wrong_review":
        metadata["security_repair"]["review_ref"] = "cas://sha256/" + "a" * 64
    elif invalid == "wrong_rescan":
        metadata["security_scan"]["last_attempt"]["report_ref"] = (
            "cas://sha256/" + "c" * 64
        )
    elif invalid == "failed_latest":
        metadata["security_scan"]["last_attempt"]["status"] = "failed"
    elif invalid == "stale_completed_run":
        metadata["security_scan"]["completed_run_id"] = "other-run"
    else:
        metadata["security_repair"]["finding_id"] = "other-finding"
    manager.mutate_task_metadata(task_id=task_id, mutate=lambda _: metadata)
    before = manager.get_task(task_id).metadata
    with pytest.raises(ToolRuntimeError) as failure:
        _h_review_report(args, ctx)
    assert failure.value.code == "POLICY_DENIED"
    assert ctx.artifacts == []
    assert manager.get_task(task_id).metadata == before
