"""Admission and evidence projection for finite scheduled source audits."""

from __future__ import annotations

from typing import Any, cast

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from openminion.modules.tool import RuntimeContext, Policy
from openminion.modules.tool import preferred_artifact_ref
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.task.cron_payloads import build_cron_request_payload
from openminion.modules.telemetry.usage import usage_event_session_id
from openminion.services.config import resolve_services_env
from openminion.tools.security.derived import comparison, export_sarif_handler
from openminion.tools.security.schemas import SecurityScanResult
from openminion.tools.security.schedule import (
    admit_run,
    configured_admission,
    clean_revision,
    report_dependencies,
    admission_fingerprint,
)


def prepare_run(
    *, runtime: Any, manager: Any, store: Any, job: dict[str, Any], run: dict[str, Any]
) -> tuple[Any, str]:
    admission = configured_admission(
        agent_id=job["agent_id"],
        env=resolve_services_env(runtime_env=runtime.config.runtime.env).snapshot(),
    )
    if admission is None or manager is None:
        raise ToolRuntimeError(
            "POLICY_DENIED", "Scheduled security configuration was revoked"
        )
    if not any(
        item["run_id"] == run["run_id"]
        and item.get("state") == "running"
        and item.get("attempts") == run.get("attempts")
        for item in store.list_cron_runs(job_id=job["job_id"], limit=100)
    ):
        raise ToolRuntimeError(
            "POLICY_DENIED", "Security run is not the current claimed attempt"
        )
    return admission, admit_run(manager, job, run, admission)


def complete_run(
    *,
    ctl: Any,
    admission: Any,
    revision: str,
    request: Any,
    job: dict[str, Any],
    run: dict[str, Any],
    brain_status: str,
) -> dict[str, Any]:
    evidence_session_id = usage_event_session_id(
        {"session_id": request.session_id, "metadata": request.meta}
    )
    records = ctl.list_recent(
        limit=100,
        scope_filters={
            "session_id": evidence_session_id,
            "agent_id": admission.agent_id,
        },
    )
    reports = [
        meta
        for meta in records
        if (meta.meta_json or {}).get("tool_name") == "security.publish_report"
    ]
    if len(reports) != 1:
        refs = [
            meta.to_ref().ref
            for meta in records
            if (meta.meta_json or {}).get("tool_name") in admission.allowed_tools
            and (meta.meta_json or {}).get("runtime_bridge")
            == "RuntimeContext.write_artifact"
        ]
        for ref in refs:
            ctl.ref_add("collection", "security-schedule:" + job["job_id"], ref)
        return {
            "job_id": job["job_id"],
            "run_id": run["run_id"],
            "revision": revision,
            "status": "failed",
            "evidence_refs": refs,
            "reason_code": "missing_or_ambiguous_report",
        }
    ref = reports[0].to_ref().ref
    report, dependencies = report_dependencies(ctl, ref, agent_id=admission.agent_id)
    try:
        unchanged = clean_revision(admission.root) == revision
    except ToolRuntimeError:
        unchanged = False
    complete = (
        brain_status == "done"
        and report.execution_status == "completed"
        and report.scope.target_revision == revision
        and unchanged
        and set(report.scope.requested_checks) == set(admission.checks)
    )
    with TemporaryDirectory(prefix="openminion-security-report-") as temporary:
        ctx = RuntimeContext(
            policy=Policy(raw={"workspace_root": str(admission.root)}),
            workspace=admission.root,
            run_root=Path(temporary).resolve(),
            artifactctl=ctl,
            scope="READ_ONLY",
            confirm=False,
            agent_id=admission.agent_id,
            session_id=evidence_session_id,
            trace_id=request.trace_id,
            permission_mode="readonly",
            tool_name="security.export_sarif",
        )
        exported = export_sarif_handler({"input_ref": ref}, ctx)
        refs = [ref, exported["artifact_ref"], *dependencies]
        comparison_ref = None
        if admission.baseline_ref:
            comparison_ref = compare_report(ctx, admission, report, ref, complete)
            refs.append(comparison_ref)
    for evidence in set(refs):
        ctl.ref_add("collection", "security-schedule:" + job["job_id"], evidence)
    return {
        "job_id": job["job_id"],
        "run_id": run["run_id"],
        "revision": revision,
        "status": "completed"
        if complete
        else "failed"
        if brain_status != "done"
        else "partial",
        "report_ref": ref,
        "sarif_ref": exported["artifact_ref"],
        "comparison_ref": comparison_ref,
        "baseline_ref": admission.baseline_ref,
    }


def bind_request(
    request: Any, security_run: Any, job: dict[str, Any], run: dict[str, Any]
) -> Any:
    admission, revision = security_run
    return replace(
        request,
        trusted_security_scan_context={
            "security_scan_context": json.dumps(
                {
                    "job_id": job["job_id"],
                    "run_id": run["run_id"],
                    "revision": revision,
                    "admission_fingerprint": admission_fingerprint(admission),
                }
            ),
            "cwd": str(admission.root),
            "workspace_root": str(admission.root),
            "permission_mode": "readonly",
            "turn_tool_allowlist": ",".join(admission.allowed_tools),
            "turn_tool_allowlist_supplied": "true",
        },
    )


def execute_security_run(
    *,
    runtime: Any,
    manager: Any,
    store: Any,
    job: dict[str, Any],
    run: dict[str, Any],
    request_builder: Any,
    artifactctl_factory: Any,
    timeout_s: float,
) -> dict[str, Any]:
    admission, revision = prepare_run(
        runtime=runtime, manager=manager, store=store, job=job, run=run
    )
    payload = {
        "message": f"Audit the configured source root {admission.root} at clean revision {revision}. "
        f"Perform exactly these checks: {', '.join(admission.checks)}. Use expected_target_revision "
        f"{revision} and include_evidence_artifact=true. Publish one canonical source report. "
        "No repair or network operations are authorized.",
        "session_id": f"cron:{job['job_id']}:{run['run_id']}",
        "conversation_id": run["run_id"],
        "timeout_seconds": admission.timeout_seconds,
        "permission_mode": "readonly",
        "allowed_tools": admission.allowed_tools,
        "deliver": False,
    }
    request_payload = build_cron_request_payload(
        job=job,
        run=run,
        message=payload.pop("message"),
        payload={**job["payload"], **payload},
        consolidation_metadata=lambda _: None,
        watch_metadata=lambda _: None,
    )
    request_payload.update(payload, agent_id=admission.agent_id)
    request = request_builder(request_payload, admission.agent_id, None, None)
    request = bind_request(request, (admission, revision), job, run)
    exposure = runtime.tools.exposure_service
    brain_session_id = usage_event_session_id(
        {"session_id": request.session_id, "metadata": request.meta}
    )
    owns_activation = not any(
        card.profile_id == "security_readonly"
        for card in exposure.cards(session_id=brain_session_id)
    )
    if owns_activation:
        exposure.activate(
            "security_readonly",
            session_id=brain_session_id,
            approved=True,
            ttl_seconds=min(
                timeout_s + admission.timeout_seconds,
                (admission.expires_at - datetime.now(UTC)).total_seconds(),
            ),
            agent_id=admission.agent_id,
            activation_reason="admitted scheduled source scan",
            policy_source=admission.schema_version,
            config_fingerprint=admission.config_fingerprint,
        )
    try:
        result = runtime.runtime_manager.submit_turn(request).result(
            timeout_s=timeout_s + admission.timeout_seconds
        )
    finally:
        if owns_activation:
            exposure.deactivate("security_readonly", session_id=brain_session_id)
    # Preserve failed-attempt artifacts; completion status is separate from report existence.
    with artifactctl_factory() as ctl:
        evidence = complete_run(
            ctl=ctl,
            admission=admission,
            revision=revision,
            request=request,
            job=job,
            run=run,
            brain_status=str(result.metadata.get("brain_status", ""))
            if not result.errors
            else "error",
        )
    return {
        "summary": f"Scheduled security scan {evidence['status']}",
        "isolated_session_id": request.session_id,
        "output": {"security_scan": evidence},
        "error": evidence["status"] != "completed",
    }


def compare_report(
    ctx: Any, admission: Any, report: Any, ref: str, complete: bool
) -> str:
    ctl = ctx.artifactctl
    baseline, _ = report_dependencies(
        ctl, admission.baseline_ref, agent_id=admission.agent_id
    )
    before = {check.check: check for check in baseline.checks}
    comparisons = []
    for check in report.checks:
        previous = before.get(check.check)
        if previous is None or not complete:
            comparisons.append(
                {
                    "check": check.check,
                    "status": "not_comparable",
                    "reason_codes": ["incomplete_or_different_checks"],
                }
            )
            continue
        old = SecurityScanResult.model_validate_json(
            ctl.read_bytes(previous.evidence_ref)
        )
        new = SecurityScanResult.model_validate_json(ctl.read_bytes(check.evidence_ref))
        comparisons.append(
            {
                "check": check.check,
                "baseline_ref": previous.evidence_ref,
                "current_ref": check.evidence_ref,
                **comparison(old, new),
            }
        )
    ctx.tool_name = "security.compare_scans"
    artifact = ctx.write_artifact(
        "security/scheduled-comparison.json",
        json.dumps(
            {
                "schema_version": "security-report-comparison/v1",
                "baseline_ref": admission.baseline_ref,
                "current_ref": ref,
                "checks": comparisons,
                "review_status": "unreviewed",
            }
        ).encode(),
        "application/json",
        durable=True,
    )
    return cast(str, preferred_artifact_ref(artifact))
