"""Trusted finite source-scan admissions carried by existing scheduled tasks."""

from __future__ import annotations

from typing import Any

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.task import TaskLifecycleState
from openminion.tools.config import get_tool_env

from .interfaces import READONLY_SECURITY_TOOLS
from .schemas import SecurityAuditCheck, SecurityAuditReport, SecurityScanResult
from .review import _read_review_artifact


class SourceScanAdmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["security-source-schedule/v1"]
    agent_id: str = Field(min_length=1)
    root: Path
    checks: list[SecurityAuditCheck] = Field(min_length=1, max_length=4)
    allowed_tools: list[str] = Field(min_length=1)
    config_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_ref: str | None = Field(
        default=None, pattern=r"^artifact://sha256/[0-9a-f]{64}$"
    )
    expires_at: AwareDatetime
    max_runs: int = Field(ge=1, le=100)
    timeout_seconds: int = Field(ge=1, le=600)

    @model_validator(mode="after")
    def finite_readonly_scope(self) -> SourceScanAdmission:
        if not self.root.is_absolute() or len(set(self.checks)) != len(self.checks):
            raise ValueError(
                "Source schedule requires an absolute root and unique checks"
            )
        if not set(self.allowed_tools) <= set(READONLY_SECURITY_TOOLS):
            raise ValueError("Source schedules permit only source scan/report tools")
        required = {
            "security.publish_report",
            *(
                {
                    "code": "security.scan_code",
                    "dependencies": "security.scan_dependencies",
                    "iac": "security.scan_artifact",
                    "secrets": "security.scan_secrets",
                }[check]
                for check in self.checks
            ),
        }
        if set(self.allowed_tools) != required:
            raise ValueError(
                "Scheduled tools must exactly match the declared checks and report publication"
            )
        if "security.publish_report" not in self.allowed_tools:
            raise ValueError("Source schedule requires canonical report publication")
        return self


def configured_admission(
    *, agent_id: str, ctx: Any = None, env: Any = None
) -> SourceScanAdmission | None:
    path = get_tool_env("OPENMINION_SECURITY_SCHEDULE_CONFIG", context=ctx, env=env)
    if not path:
        return None
    records = json.loads(Path(path).read_bytes())
    if agent_id not in records:
        return None
    admission = SourceScanAdmission.model_validate(records[agent_id])
    if admission.agent_id != agent_id:
        raise ToolRuntimeError("POLICY_DENIED", "Scheduled scan agent identity differs")
    scan_config = get_tool_env("OPENMINION_SECURITY_SCAN_CONFIG", context=ctx, env=env)
    rules = get_tool_env("OPENMINION_SECURITY_SEMGREP_CONFIG", context=ctx, env=env)
    if not scan_config or not rules:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "Scheduled scans require pinned worker configuration and local rules",
        )
    fingerprint = hashlib.sha256(
        Path(scan_config).read_bytes() + Path(rules).read_bytes()
    ).hexdigest()
    if fingerprint != admission.config_fingerprint:
        raise ToolRuntimeError(
            "POLICY_DENIED", "Scheduled scan asset configuration changed"
        )
    return admission


def clean_revision(root: Path) -> str:
    from .plugin import _git_output

    if (
        Path(_git_output(root, "rev-parse", "--show-toplevel")).resolve()
        != root.resolve()
    ):
        raise ToolRuntimeError(
            "POLICY_DENIED", "Scheduled source root is not a Git root"
        )
    if _git_output(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ToolRuntimeError("POLICY_DENIED", "Scheduled source root is dirty")
    return _git_output(root, "rev-parse", "HEAD").lower()


def admission_fingerprint(admission: SourceScanAdmission) -> str:
    return hashlib.sha256(admission.model_dump_json().encode()).hexdigest()


def admit_run(
    manager: Any, job: dict[str, Any], run: dict[str, Any], current: SourceScanAdmission
) -> str:
    admitted = SourceScanAdmission.model_validate(job["payload"]["security_scan"])
    record = manager.get_task_by_job(job["job_id"])
    if (
        record is None
        or current != admitted
        or current.agent_id != job.get("agent_id")
        or record.metadata.get("security_scan_admission")
        != admission_fingerprint(admitted)
    ):
        raise ToolRuntimeError(
            "POLICY_DENIED", "Scheduled scan admission changed or is untrusted"
        )
    live_job = manager.get_scheduled_job(job["job_id"])
    if (
        record.state != TaskLifecycleState.ACTIVE
        or not live_job
        or not live_job["enabled"]
    ):
        raise ToolRuntimeError(
            "POLICY_DENIED", "Scheduled scan is cancelled, paused, or exhausted"
        )
    if current.expires_at <= datetime.now(UTC):
        manager.set_scheduled_job_enabled(record.task_id, enabled=False)
        raise ToolRuntimeError("POLICY_DENIED", "Scheduled scan admission expired")
    run_id = str(run["run_id"])
    if run.get("state") != "running" or run.get("job_id") != job["job_id"]:
        raise ToolRuntimeError(
            "POLICY_DENIED", "Scheduled scan requires its active claimed run"
        )

    def consume(metadata: dict[str, Any]) -> dict[str, Any]:
        state = dict(metadata.get("security_scan") or {})
        if state.get("last_admitted_run_id") == run_id:
            raise ToolRuntimeError(
                "POLICY_DENIED", "Scheduled scan run was already admitted"
            )
        count = int(state.get("admitted_runs", 0))
        if count >= current.max_runs:
            raise ToolRuntimeError(
                "POLICY_DENIED", "Scheduled scan run budget exhausted"
            )
        state.update(
            admitted_runs=count + 1,
            last_admitted_run_id=run_id,
            baseline_ref=current.baseline_ref,
            status="running",
        )
        return {**metadata, "security_scan": state}

    updated = manager.mutate_task_metadata(task_id=record.task_id, mutate=consume)
    if updated.metadata["security_scan"]["admitted_runs"] >= current.max_runs:
        manager.set_scheduled_job_enabled(record.task_id, enabled=False)
    return clean_revision(current.root)


def report_dependencies(
    ctl: Any, ref: str, *, agent_id: str
) -> tuple[SecurityAuditReport, list[str]]:
    content, meta = _read_review_artifact(ctl, ref, "security.publish_report")
    if meta.agent_id != agent_id:
        raise ToolRuntimeError(
            "POLICY_DENIED", "Baseline report belongs to a different agent"
        )
    report = SecurityAuditReport.model_validate_json(content)
    refs = [ref, *report.evidence_refs]
    for check in report.checks:
        content, scan_meta = _read_review_artifact(
            ctl, check.evidence_ref, check.tool_id
        )
        scan = SecurityScanResult.model_validate_json(content)
        if (
            scan_meta.session_id != meta.session_id
            or scan_meta.agent_id != meta.agent_id
            or scan.target_revision != report.scope.target_revision
        ):
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", "Baseline evidence does not belong to its report"
            )
    return report, refs


def pin_baseline(ctl: Any, *, job_id: Any, admission: Any) -> None:
    if admission.baseline_ref is None:
        return
    _, refs = report_dependencies(
        ctl, admission.baseline_ref, agent_id=admission.agent_id
    )
    for ref in refs:
        ctl.ref_add("collection", "security-schedule:" + job_id, ref)
