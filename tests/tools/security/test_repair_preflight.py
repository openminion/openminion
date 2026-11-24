"""Operator-procedure fixtures, not runtime project admission enforcement."""

import json

import pytest

from openminion.base.redaction import redact_sensitive_text
from openminion.modules.artifact.errors import ArtifactCtlError
from openminion.tools.security.review_schemas import SecurityFindingReview
from openminion.tools.security.schemas import SecurityAuditReport
from openminion.tools.security.plugin import _h_review_report
from tests.tools.security.test_audit_report import audit_context as audit_context
from tests.tools.security.test_review_report import _initial, _source, _reviewer


def operator_handoff(
    ctx, source_ref, review_ref, *, base, before_exit_code, paths, commands
):
    """Apply the documented checklist in a fixture before calling project launch."""
    source = SecurityAuditReport.model_validate_json(
        ctx.artifactctl.read_bytes(source_ref)
    )
    review = SecurityFindingReview.model_validate_json(
        ctx.artifactctl.read_bytes(review_ref)
    )
    assert not ctx.artifactctl.get(source_ref).deleted_at
    assert not ctx.artifactctl.get(review_ref).deleted_at
    assert review.source_report_ref == source_ref and review.review_kind == "initial"
    assert review.scope.target_revision == source.scope.target_revision == base
    assert (
        ctx.artifactctl.get(review_ref).meta_json["tool_name"]
        == "security.review_report"
    )
    assert review.reviewer_agent_id != ctx.artifactctl.get(source_ref).agent_id
    assert len(review.remediation_brief) == 1
    finding = review.remediation_brief[0]
    assert {location.path for location in finding.locations} <= set(paths)
    assert before_exit_code == 1 and commands
    handoff = json.dumps(
        {
            "report_ref": source_ref,
            "review_ref": review_ref,
            "finding_id": finding.finding_id,
            "review_id": review.review_id,
            "remediation_brief": finding.model_dump(mode="json", exclude_none=True),
            "verification_timeout_seconds": 20,
            "delivery_expectations": "Passing verifier and independent child review before parent acceptance.",
            "base_revision": base,
            "allowed_paths": paths,
            "verification_commands": commands,
            "failing_before_exit_code": before_exit_code,
            "operator_approved": True,
        }
    )
    redacted, count = redact_sensitive_text(handoff)
    assert not count and len(redacted) <= 4000
    return redacted


@pytest.mark.parametrize(
    "invalid",
    [
        "unreviewed",
        "deleted",
        "stale",
        "unaccepted",
        "passing_before",
        "scope",
        "missing_verifier",
        "overflow",
        "redaction",
    ],
)
def test_operator_withholds_launch_for_incomplete_repair_handoff(
    audit_context, invalid
):
    ctx, base = audit_context
    source = _source(ctx, base)
    args = _initial(source)
    if invalid == "unaccepted":
        args["finding_reviews"][0] = {
            "finding_id": "F-1",
            "decision": "rejected",
            "rationale": "Unsupported.",
        }
    if invalid == "overflow":
        args["finding_reviews"][0]["verification_criteria"] = ["x" * 1000] * 5
    review = _h_review_report(args, _reviewer(ctx))["review_ref"]
    if invalid == "deleted":
        ctx.artifactctl.delete(review)
    options = dict(
        base=base,
        before_exit_code=1,
        paths=["app.py"],
        commands=["python -B verify.py"],
    )
    if invalid == "unreviewed":
        review = source
    if invalid == "stale":
        options["base"] = "b" * 40
    if invalid == "passing_before":
        options["before_exit_code"] = 0
    if invalid == "scope":
        options["paths"] = ["other.py"]
    if invalid == "missing_verifier":
        options["commands"] = []
    if invalid == "redaction":
        options["commands"] = ["verify --token=fixture-secret"]
    with pytest.raises((AssertionError, ValueError, ArtifactCtlError)):
        operator_handoff(ctx, source, review, **options)


def paused_security_repair(
    ctx, revision, manager, *, root, report_ref=None, review_ref=None
):
    import hashlib
    from openminion.tools.security.review_schemas import SecurityRepairSelection
    from openminion.tools.security.plugin import _h_publish_report
    from tests.tools.security.test_audit_report import _scan, _publish_args
    from tests.tools.security.test_schedule import admission

    assets = root / "repair-assets"
    assets.mkdir()
    worker = assets / "worker.json"
    worker.write_text("{}")
    rules = assets / "rules.yml"
    rules.write_bytes((ctx.workspace / "rule.yml").read_bytes())
    scope = admission(ctx.workspace).model_copy(
        update={
            "agent_id": "researcher",
            "config_fingerprint": hashlib.sha256(
                worker.read_bytes() + rules.read_bytes()
            ).hexdigest(),
        }
    )
    config = assets / "admission.json"
    config.write_text(json.dumps({scope.agent_id: scope.model_dump(mode="json")}))
    env = {
        "OPENMINION_SECURITY_SCAN_CONFIG": str(worker),
        "OPENMINION_SECURITY_SEMGREP_CONFIG": str(rules),
        "OPENMINION_SECURITY_SCHEDULE_CONFIG": str(config),
    }
    created = manager.schedule_user_task(
        name="source",
        instruction="scan",
        agent_id=scope.agent_id,
        schedule={"kind": "every", "every_ms": 60000},
        security_scan=scope.model_dump(mode="json"),
    )
    job_id = created["job"]["job_id"]
    run_id = "scan-run"
    report = report_ref
    if report is None:
        ctx.agent_id = scope.agent_id
        ctx.session_id = f"cron:{job_id}:{run_id}"
        ctx.trace_id = "scan-trace"
        ctx.tool_name = "security.scan_code"
        scan_ref = ctx.write_artifact(
            "security/scan.json",
            _scan(check="code", revision=revision).model_dump_json().encode(),
            "application/json",
            durable=True,
        ).canonical_ref
        ctx.tool_name = "security.publish_report"
        report = _h_publish_report(
            _publish_args(
                revision=revision,
                refs={"code": scan_ref},
                findings=[
                    {
                        "finding_id": "F-1",
                        "basis": "manual",
                        "disposition": "candidate",
                        "title": "Fixture input validation",
                        "category": "validation",
                        "severity": "medium",
                        "confidence": "high",
                        "description": "Fixture accepts invalid input.",
                        "locations": [{"path": "app.py", "line": 1}],
                        "evidence_summary": "Approved reproducer.",
                    }
                ],
            ),
            ctx,
        )["report_ref"]
    review = (
        review_ref or _h_review_report(_initial(report), _reviewer(ctx))["review_ref"]
    )
    manager.record_scheduled_outcome(
        cron_job_id=job_id,
        run={
            "run_id": run_id,
            "state": "finished",
            "attempts": 1,
            "output": {
                "security_scan": {
                    "job_id": job_id,
                    "run_id": run_id,
                    "status": "completed",
                    "report_ref": report,
                    "revision": revision,
                }
            },
        },
    )
    manager.pause_task(created["record"].task_id)
    selection = SecurityRepairSelection(
        source_task_id=created["record"].task_id,
        review_ref=review,
        finding_id="F-1",
        write_paths=("app.py",),
    )
    return selection, env


@pytest.fixture
def repair_admission(audit_context, tmp_path):  # noqa: F811
    from tests.task.test_lifecycle import _manager
    from tests.tools.security.test_audit_report import _git

    ctx, revision = audit_context
    manager = _manager(tmp_path)
    selection, env = paused_security_repair(ctx, revision, manager, root=tmp_path)
    candidate = tmp_path / "candidate"
    _git("worktree", "add", "--detach", str(candidate), revision, cwd=ctx.workspace)
    try:
        yield ctx, manager, candidate, selection, env
    finally:
        manager.close()


def test_actual_security_repair_admission_binds_canonical_source(repair_admission):
    from openminion.tools.security.review import validate_security_repair

    ctx, manager, candidate, selection, env = repair_admission
    context = validate_security_repair(
        selection,
        manager=manager,
        ctl=ctx.artifactctl,
        repository=candidate,
        agent_id="repairer",
        env=env,
    )
    assert context.repository == str(candidate)
    assert context.source_root == str(ctx.workspace)
    assert context.write_paths == ("app.py",)
    assert set(context.protected_files) == {"rule.yml"}
    assert context.review_ref in context.evidence_refs
    assert context.source_run_id == "scan-run"


@pytest.mark.parametrize(
    "invalid",
    [
        "unpaused",
        "latest_failed",
        "deleted",
        "foreign",
        "dirty",
        "wrong_base",
        "paths",
        "directory",
        "config",
        "rejected",
        "needs_more_evidence",
        "self_review",
    ],
)
def test_actual_security_repair_admission_rejects_invalid_source(
    repair_admission, invalid
):
    from openminion.tools.security.review import validate_security_repair
    from openminion.modules.tool.errors import ToolRuntimeError
    from tests.tools.security.test_audit_report import _git

    ctx, manager, candidate, selection, env = repair_admission
    record = manager.get_task(selection.source_task_id)
    if invalid == "unpaused":
        manager.set_scheduled_job_enabled(record.cron_job_id, enabled=True)
    elif invalid == "latest_failed":
        manager.record_scheduled_outcome(
            cron_job_id=record.cron_job_id,
            run={"run_id": "failed", "state": "failed", "attempts": 1},
        )
    elif invalid == "deleted":
        ctx.artifactctl.delete(selection.review_ref)
    elif invalid == "foreign":
        selection = selection.model_copy(update={"source_task_id": "foreign"})
    elif invalid == "dirty":
        (ctx.workspace / "app.py").write_text("changed")
    elif invalid == "wrong_base":
        _git("commit", "--allow-empty", "-m", "changed", cwd=candidate)
    elif invalid in {"paths", "directory"}:
        selection = selection.model_copy(
            update={"write_paths": ("rule.yml",) if invalid == "paths" else (".",)}
        )
    elif invalid == "config":
        from pathlib import Path

        Path(env["OPENMINION_SECURITY_SCAN_CONFIG"]).write_text('{"changed":true}')
    elif invalid in {"rejected", "needs_more_evidence"}:
        from tests.tools.security.test_review_report import _decision

        review = SecurityFindingReview.model_validate_json(
            ctx.artifactctl.read_bytes(selection.review_ref)
        )
        result = _h_review_report(
            _initial(review.source_report_ref, [_decision(decision=invalid)]),
            _reviewer(ctx),
        )
        selection = selection.model_copy(update={"review_ref": result["review_ref"]})
    elif invalid == "self_review":
        from tests.tools.security.test_review_report import _rewrite

        ref = _rewrite(
            ctx,
            selection.review_ref,
            lambda payload: payload.update(reviewer_agent_id="researcher"),
            agent_id="researcher",
        )
        selection = selection.model_copy(update={"review_ref": ref})
    with pytest.raises(ToolRuntimeError) as failure:
        validate_security_repair(
            selection,
            manager=manager,
            ctl=ctx.artifactctl,
            repository=candidate,
            agent_id="repairer",
            env=env,
        )
    if invalid in {"rejected", "needs_more_evidence", "self_review"}:
        assert failure.value.code == "POLICY_DENIED"
        assert failure.value.message == (
            "a report author cannot review their report"
            if invalid == "self_review"
            else "repair scope must cover one accepted finding"
        )
