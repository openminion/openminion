"""Publish independent decisions over immutable source-security reports."""

import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, cast

from pydantic import ValidationError

from openminion.base.redaction import redact_mapping
from openminion.base.time import utc_now_iso
from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.artifact.errors import ArtifactCtlError
from openminion.modules.artifact.models import ArtifactMeta
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool import RuntimeContext
from openminion.modules.task import TaskLifecycleRecord, TaskLifecycleState, TaskManager
from openminion.modules.task.project.models import SecurityRepairContext
from openminion.modules.task.project.verification import security_repair_file_identity

from .interfaces import TOOL_SECURITY_PUBLISH_REPORT, TOOL_SECURITY_REVIEW_REPORT
from .schemas import (
    SecurityAuditFinding,
    SecurityAuditReport,
)
from .review_schemas import (
    SecurityFindingDecision,
    SecurityFindingReview,
    SecurityFollowUpReviewArgs,
    SecurityInitialReviewArgs,
    SecurityRemediationFinding,
    SecurityRepairSelection,
)

if TYPE_CHECKING:
    from .schedule import SourceScanAdmission


def _repair_schedule(
    selection: SecurityRepairSelection, manager: TaskManager, env: Any
) -> tuple["SourceScanAdmission", str, dict[str, Any]]:
    from .schedule import (
        SourceScanAdmission,
        admission_fingerprint,
        configured_admission,
    )

    record = manager.get_task(selection.source_task_id)
    if record is None or not record.cron_job_id:
        raise ToolRuntimeError(
            "POLICY_DENIED", "repair requires a scheduled source task"
        )
    job = manager.get_scheduled_job(record.cron_job_id)
    if job is None or job["enabled"] or record.state != TaskLifecycleState.PAUSED:
        raise ToolRuntimeError("POLICY_DENIED", "source schedule must be paused")
    configured = configured_admission(agent_id=job["agent_id"], env=env)
    admitted = SourceScanAdmission.model_validate(job["payload"].get("security_scan"))
    if (
        configured != admitted
        or admitted.checks != ["code"]
        or admitted.agent_id != record.agent_id
        or record.metadata.get("security_scan_admission")
        != admission_fingerprint(admitted)
        or admitted.expires_at <= datetime.now(UTC)
    ):
        raise ToolRuntimeError(
            "POLICY_DENIED", "source scan admission changed or expired"
        )
    if manager.list_scheduled_runs(job_id=job["job_id"], states=["running"], limit=1):
        raise ToolRuntimeError("POLICY_DENIED", "source scan is still running")
    state = record.metadata.get("security_scan") or {}
    attempt = state.get("last_attempt") or {}
    if (
        state.get("status") != "completed"
        or attempt.get("status") != "completed"
        or not attempt.get("run_id")
        or attempt.get("run_id") != state.get("completed_run_id")
        or attempt.get("job_id") != job["job_id"]
        or attempt.get("report_ref") != state.get("report_ref")
    ):
        raise ToolRuntimeError(
            "POLICY_DENIED", "repair requires the latest complete scan"
        )
    return admitted, job["job_id"], attempt


def _repair_worktree(
    root: Path, repository: Path, base: str, paths: tuple[str, ...]
) -> dict[str, str]:
    from .plugin import _git_output
    from .schedule import clean_revision

    if (
        repository == root
        or root in repository.parents
        or clean_revision(repository) != base
        or _git_output(
            repository, "rev-parse", "--path-format=absolute", "--git-common-dir"
        )
        != _git_output(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    ):
        raise ToolRuntimeError(
            "POLICY_DENIED", "repair requires a separate worktree at the reviewed base"
        )
    tracked = set(_git_output(repository, "ls-files", "-z").split("\0")) - {""}
    if len(paths) != len(set(paths)):
        raise ToolRuntimeError("INVALID_ARGUMENT", "repair write paths must be unique")
    for name in paths:
        path = PurePosixPath(name)
        target = repository / name
        if (
            path.is_absolute()
            or ".." in path.parts
            or str(path) != name
            or any(char in name for char in "*?[]\\")
            or name not in tracked
            or not target.is_file()
            or target.resolve() != target
        ):
            raise ToolRuntimeError(
                "POLICY_DENIED", "repair write paths must name exact tracked files"
            )
    return {
        name: security_repair_file_identity(repository / name)
        for name in sorted(tracked - set(paths))
    }


def validate_security_repair(
    selection: SecurityRepairSelection,
    *,
    manager: TaskManager,
    ctl: ArtifactCtl,
    repository: Path,
    agent_id: str,
    env: Any,
) -> SecurityRepairContext:
    from .schedule import clean_revision, report_dependencies

    if not agent_id:
        raise ToolRuntimeError(
            "POLICY_DENIED", "repair requires an operator-selected agent"
        )
    admission, job_id, attempt = _repair_schedule(selection, manager, env)
    content, meta = _read_review_artifact(
        ctl, selection.review_ref, TOOL_SECURITY_REVIEW_REPORT
    )
    review = SecurityFindingReview.model_validate_json(content)
    report, author = _read_source_report(
        ctl, review.source_report_ref, review.reviewer_agent_id
    )
    root = admission.root.resolve()
    base = clean_revision(root)
    if (
        review.review_kind != "initial"
        or (
            review.reviewer_agent_id,
            review.reviewer_session_id,
            review.reviewer_trace_id,
        )
        != (meta.agent_id, meta.session_id, meta.trace_id)
        or review.report_author_agent_id != author.agent_id
        or author.agent_id != admission.agent_id
        or review.source_report_ref != attempt.get("report_ref")
        or review.scope != report.scope
        or review.assessment_id != report.assessment_id
        or report.scope.target_revision != base
        or attempt.get("revision") != base
        or (root / report.scope.target).resolve() != root
        or report.scope.requested_checks != admission.checks
    ):
        raise ToolRuntimeError(
            "POLICY_DENIED", "review does not match the current scheduled source"
        )
    findings = _candidate_findings(report)
    _exact_review_ids(
        [item.finding_id for item in review.finding_reviews], set(findings)
    )
    accepted = _accepted_brief(findings, review.finding_reviews)
    finding = next(
        (item for item in accepted if item.finding_id == selection.finding_id), None
    )
    if (
        finding is None
        or not finding.locations
        or not {item.path for item in finding.locations} <= set(selection.write_paths)
    ):
        raise ToolRuntimeError(
            "POLICY_DENIED", "repair scope must cover one accepted finding"
        )
    repository = repository.resolve()
    protected = _repair_worktree(root, repository, base, selection.write_paths)
    _, refs = report_dependencies(
        ctl, review.source_report_ref, agent_id=admission.agent_id
    )
    return SecurityRepairContext(
        source_task_id=selection.source_task_id,
        source_job_id=job_id,
        source_run_id=attempt["run_id"],
        report_ref=review.source_report_ref,
        review_ref=selection.review_ref,
        finding_id=selection.finding_id,
        source_root=str(root),
        base_revision=base,
        config_fingerprint=admission.config_fingerprint,
        repository=str(repository),
        write_paths=selection.write_paths,
        protected_files=protected,
        evidence_refs=tuple(dict.fromkeys([selection.review_ref, *refs])),
        remediation_objective=finding.remediation_objective,
        verification_criteria=tuple(finding.verification_criteria),
    )


def _read_review_artifact(
    ctl: ArtifactCtl, ref: str, producer: str
) -> tuple[bytes, ArtifactMeta]:
    try:
        meta = ctl.get(ref)
        provenance = meta.meta_json or {}
        if (
            meta.deleted_at
            or not meta.agent_id
            or not meta.session_id
            or not meta.trace_id
            or provenance.get("runtime_bridge") != "RuntimeContext.write_artifact"
            or provenance.get("tool_name") != producer
        ):
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", "review evidence has invalid provenance"
            )
        return ctl.read_bytes(ref), meta
    except (ArtifactCtlError, OSError) as exc:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", "review evidence is unavailable"
        ) from exc


def _read_source_report(
    ctl: ArtifactCtl, ref: str, reviewer: str
) -> tuple[SecurityAuditReport, ArtifactMeta]:
    content, meta = _read_review_artifact(ctl, ref, TOOL_SECURITY_PUBLISH_REPORT)
    report = SecurityAuditReport.model_validate_json(content)
    if meta.agent_id == reviewer:
        raise ToolRuntimeError(
            "POLICY_DENIED", "a report author cannot review their report"
        )
    checks = report.checks
    if (
        report.execution_status != "completed"
        or len(checks) != len(report.scope.requested_checks)
        or {check.check for check in checks} != set(report.scope.requested_checks)
        or any(
            check.status != "completed" or check.truncated or check.error is not None
            for check in checks
        )
    ):
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", "review requires complete untruncated checks"
        )
    for evidence_ref in report.evidence_refs:
        try:
            if ctl.get(evidence_ref).deleted_at:
                raise ToolRuntimeError("INVALID_ARGUMENT", "source evidence is deleted")
            ctl.read_bytes(evidence_ref)
        except (ArtifactCtlError, OSError) as exc:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", "source evidence is unavailable"
            ) from exc
    return report, meta


def _exact_review_ids(actual: list[str], expected: set[str]) -> None:
    if len(actual) != len(expected) or set(actual) != expected:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "review decisions must cover each required finding exactly once",
            details={
                "expected_finding_ids": sorted(expected),
                "received_finding_ids": actual,
            },
        )


def _candidate_findings(report: SecurityAuditReport) -> dict[str, SecurityAuditFinding]:
    return {
        finding.finding_id: finding
        for finding in report.findings
        if finding.disposition == "candidate"
    }


def _accepted_brief(
    findings: dict[str, SecurityAuditFinding], decisions: list[SecurityFindingDecision]
) -> list[SecurityRemediationFinding]:
    return [
        SecurityRemediationFinding(
            **findings[item.finding_id].model_dump(
                include={
                    "finding_id",
                    "title",
                    "category",
                    "severity",
                    "confidence",
                    "locations",
                    "evidence_ref",
                    "scanner_finding_ids",
                    "evidence_summary",
                }
            ),
            remediation_objective=cast(str, item.remediation_objective),
            verification_criteria=cast(list[str], item.verification_criteria),
        )
        for item in decisions
        if item.decision == "accepted"
    ]


def _initial_review(
    args: SecurityInitialReviewArgs, ctl: ArtifactCtl, reviewer: str
) -> dict[str, Any]:
    report, meta = _read_source_report(ctl, args.report_ref, reviewer)
    candidates = _candidate_findings(report)
    if not candidates:
        raise ToolRuntimeError("INVALID_ARGUMENT", "report has no candidates to review")
    _exact_review_ids(
        [item.finding_id for item in args.finding_reviews], set(candidates)
    )
    statuses = {item.decision for item in args.finding_reviews}
    return {
        "source_report_ref": args.report_ref,
        "assessment_id": report.assessment_id,
        "scope": report.scope,
        "report_author_agent_id": meta.agent_id,
        "review_status": next(iter(statuses)) if len(statuses) == 1 else "mixed",
        "finding_reviews": args.finding_reviews,
        "evidence_refs": [args.report_ref, *report.evidence_refs],
        "remediation_brief": _accepted_brief(candidates, args.finding_reviews),
    }


def _matching_rescan(
    original: SecurityAuditReport, current: SecurityAuditReport
) -> None:
    if (
        original.scope.target != current.scope.target
        or original.scope.target_revision.lower()
        == current.scope.target_revision.lower()
        or set(original.scope.requested_checks) != set(current.scope.requested_checks)
    ):
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", "rescan target, revision, or check set is incompatible"
        )
    previous = {check.check: check for check in original.checks}
    for check in current.checks:
        old = previous[check.check]
        if (
            check.tool_id != old.tool_id
            or check.scanner != old.scanner
            or check.scanner_version != old.scanner_version
            or check.configuration_identity != old.configuration_identity
        ):
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", "rescan scanner configuration is incompatible"
            )


def _follow_up_review(
    args: SecurityFollowUpReviewArgs, ctl: ArtifactCtl, reviewer: str
) -> dict[str, Any]:
    content, _ = _read_review_artifact(
        ctl, args.prior_review_ref, TOOL_SECURITY_REVIEW_REPORT
    )
    prior = SecurityFindingReview.model_validate_json(content)
    if prior.review_kind != "initial":
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", "follow-up requires an initial review"
        )
    original, author = _read_source_report(ctl, prior.source_report_ref, reviewer)
    current, _ = _read_source_report(ctl, args.rescan_report_ref, reviewer)
    _matching_rescan(original, current)
    accepted = {
        item.finding_id for item in prior.finding_reviews if item.decision == "accepted"
    }
    if not accepted:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", "prior review has no accepted findings"
        )
    _exact_review_ids([item.finding_id for item in args.closure_reviews], accepted)
    current_candidates = set(_candidate_findings(current))
    matched = {
        finding_id
        for item in args.closure_reviews
        for finding_id in item.current_finding_ids
    }
    if not matched <= current_candidates:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", "closure cites an unknown current candidate"
        )
    _exact_review_ids(
        [item.finding_id for item in args.new_finding_reviews],
        current_candidates - matched,
    )
    statuses = {item.outcome for item in args.closure_reviews}
    actionable = any(item.decision != "rejected" for item in args.new_finding_reviews)
    return {
        "source_report_ref": prior.source_report_ref,
        "assessment_id": original.assessment_id,
        "scope": original.scope,
        "report_author_agent_id": author.agent_id,
        "review_status": next(iter(statuses))
        if len(statuses) == 1 and not actionable
        else "mixed",
        "closure_reviews": args.closure_reviews,
        "new_finding_reviews": args.new_finding_reviews,
        "prior_review_ref": args.prior_review_ref,
        "rescan_report_ref": args.rescan_report_ref,
        "rescan_revision": current.scope.target_revision,
        "evidence_refs": list(
            dict.fromkeys(
                [
                    args.prior_review_ref,
                    prior.source_report_ref,
                    args.rescan_report_ref,
                    *original.evidence_refs,
                    *current.evidence_refs,
                ]
            )
        ),
    }


def _redact_review_prose(report: SecurityFindingReview) -> SecurityFindingReview:
    payload = report.model_dump(mode="json", exclude_none=True)
    prose, count = redact_mapping(
        {"summary": payload["summary"], "limitations": payload["limitations"]}
    )
    payload.update(prose)
    for section in (
        "finding_reviews",
        "closure_reviews",
        "new_finding_reviews",
        "remediation_brief",
    ):
        for item in payload[section]:
            prose, redacted = redact_mapping(
                {
                    key: item[key]
                    for key in (
                        "rationale",
                        "remediation_objective",
                        "verification_criteria",
                        "evidence_requests",
                        "next_step_guidance",
                    )
                    if key in item
                }
            )
            item.update(prose)
            count += redacted
    try:
        report = SecurityFindingReview.model_validate(
            {**payload, "redaction_count": count}
        )
    except ValidationError as exc:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "redacted review exceeds field limits; shorten the prose",
        ) from exc
    return report


def _follow_up_source(
    args: SecurityFollowUpReviewArgs, ctx: RuntimeContext
) -> TaskLifecycleRecord:
    record = (
        ctx.task_manager.get_task(args.source_task_id)
        if ctx.task_manager is not None
        else None
    )
    if record is None or not record.cron_job_id:
        raise ToolRuntimeError("POLICY_DENIED", "follow-up source task is unavailable")
    _validate_follow_up_link(record.metadata, args)
    return record


def _validate_follow_up_link(
    metadata: dict[str, Any], args: SecurityFollowUpReviewArgs
) -> None:
    repair = metadata.get("security_repair") or {}
    scan = metadata.get("security_scan") or {}
    attempt = scan.get("last_attempt") or {}
    if (
        repair.get("review_ref") != args.prior_review_ref
        or scan.get("status") != "completed"
        or attempt.get("status") != "completed"
        or attempt.get("run_id") != scan.get("completed_run_id")
        or attempt.get("report_ref") != args.rescan_report_ref
        or repair.get("finding_id")
        not in {item.finding_id for item in args.closure_reviews}
    ):
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "follow-up does not match the source repair and latest rescan",
        )


def _link_follow_up(
    args: SecurityFollowUpReviewArgs, ctx: RuntimeContext, ref: str
) -> None:
    record = _follow_up_source(args, ctx)

    def link(metadata: dict[str, Any]) -> dict[str, Any]:
        _validate_follow_up_link(metadata, args)
        repair = metadata["security_repair"]
        outcome = next(
            item.outcome
            for item in args.closure_reviews
            if item.finding_id == repair["finding_id"]
        )
        return {
            **metadata,
            "security_repair": {
                **repair,
                "follow_up_review_ref": ref,
                "rescan_report_ref": args.rescan_report_ref,
                "finding_outcome": outcome,
            },
        }

    ctx.task_manager.mutate_task_metadata(task_id=record.task_id, mutate=link)
    ctx.artifactctl.ref_add(
        "collection", "security-schedule:" + record.cron_job_id, ref
    )


def publish_security_review(
    args: SecurityInitialReviewArgs | SecurityFollowUpReviewArgs, *, ctx: RuntimeContext
) -> dict[str, Any]:
    if ctx.permission_mode != "readonly":
        raise ToolRuntimeError(
            "POLICY_DENIED", "security review requires readonly permission"
        )
    if not ctx.agent_id or not ctx.session_id or not ctx.trace_id:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "security review requires runtime agent, session and trace identity",
        )
    if ctx.artifactctl is None:
        raise ToolRuntimeError(
            "INTERNAL_ERROR", "security review requires canonical artifact storage"
        )
    if isinstance(args, SecurityFollowUpReviewArgs) and args.source_task_id is not None:
        _follow_up_source(args, ctx)
    try:
        values = (
            _initial_review(args, ctx.artifactctl, ctx.agent_id)
            if isinstance(args, SecurityInitialReviewArgs)
            else _follow_up_review(args, ctx.artifactctl, ctx.agent_id)
        )
    except ValidationError as exc:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", "review evidence has an invalid schema"
        ) from exc
    report = SecurityFindingReview(
        review_id=uuid.uuid4().hex,
        review_kind=args.review_kind,
        created_at=utc_now_iso(),
        reviewer_agent_id=ctx.agent_id,
        reviewer_session_id=ctx.session_id,
        reviewer_trace_id=ctx.trace_id,
        summary=args.summary,
        limitations=args.limitations,
        redaction_count=0,
        **values,
    )
    report = _redact_review_prose(report)
    artifact = ctx.write_artifact(
        f"security/review-{report.review_id}.json",
        report.model_dump_json(exclude_none=True, indent=2).encode(),
        "application/json",
        durable=True,
    )
    if artifact.canonical_ref is None:
        raise ToolRuntimeError(
            "INTERNAL_ERROR",
            "security review publication requires a canonical artifact",
        )
    if isinstance(args, SecurityFollowUpReviewArgs) and args.source_task_id is not None:
        _link_follow_up(args, ctx, artifact.canonical_ref)
    return {
        "ok": True,
        "review_id": report.review_id,
        "schema_version": report.schema_version,
        "review_kind": report.review_kind,
        "review_status": report.review_status,
        "finding_count": len(report.finding_reviews)
        + len(report.closure_reviews)
        + len(report.new_finding_reviews),
        "accepted_count": sum(
            item.decision == "accepted"
            for item in (*report.finding_reviews, *report.new_finding_reviews)
        ),
        "redaction_count": report.redaction_count,
        "review_ref": artifact.canonical_ref,
        "source_report_ref": report.source_report_ref,
        "target_revision": report.scope.target_revision,
        "remediation_brief": [
            item.model_dump(mode="json", exclude_none=True)
            for item in report.remediation_brief
        ],
        "artifact_ref": artifact.canonical_ref,
        "artifact_refs": [artifact.canonical_ref],
        "content": f"Published security review with status={report.review_status}",
    }
