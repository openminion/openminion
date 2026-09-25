from __future__ import annotations

import importlib.util
import platform
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, cast

from openminion.base.time import utc_now_iso
from openminion.modules.policy.models import PolicyDecision, RiskSpec
from openminion.modules.runtime.credentials import CredentialRef
from openminion.modules.tool.contracts.schemas import TOOL_ERROR_CONFIRM_REQUIRED
from openminion.modules.tool.errors import ToolRuntimeError

from .contracts import (
    CommandPlan,
    EvidenceRecord,
    JobStatus,
    OperationJob,
    OperationRequest,
    OperationTarget,
    TargetPlatform,
    TransportResult,
)
from .evidence import EvidenceStore, build_evidence
from .interfaces import FileReadTransport, TargetTransport
from .jobs import OperationJobStore
from .plans import CommandPlanStore, build_command_plan, validate_command_plan
from .policy import OperationPolicyDecision, decide_operation_policy
from .profiles import build_argv
from .registry import TargetRegistry
from .transports import build_transports, registration_map
from .transports.ssh import SshConnectionError

RedactionResolver = Callable[[OperationTarget], tuple[str, ...]]
_TRANSPORT_DEPENDENCIES = {
    "ssh": "asyncssh",
    "winrm": "winrm",
    "kubernetes": "kubernetes",
    "ssm": "boto3",
}


class OpsService:
    def __init__(
        self,
        *,
        targets: TargetRegistry | None = None,
        transports: Mapping[str, TargetTransport] | None = None,
        jobs: OperationJobStore | None = None,
        evidence: EvidenceStore | None = None,
        plans: CommandPlanStore | None = None,
        redaction_resolver: RedactionResolver | None = None,
        transport_capabilities: Mapping[str, frozenset[str]] | None = None,
        action_policy: Any | None = None,
    ) -> None:
        self.targets = targets or TargetRegistry()
        self.jobs = jobs or OperationJobStore()
        self.evidence = evidence or EvidenceStore()
        self.plans = plans or CommandPlanStore()
        self.action_policy = action_policy
        self._redaction_resolver = redaction_resolver or (lambda _target: ())
        if transports is None:
            self._transports = build_transports(
                ("local", "container"),
                credential_reader=lambda _ref: "",
            )
            self._transport_capabilities: dict[str, frozenset[str]] = {
                kind: registration.capabilities
                for kind, registration in registration_map().items()
                if kind in self._transports
            }
        else:
            self._transports = dict(transports)
            self._transport_capabilities = dict(transport_capabilities or {})

    def list_targets(self) -> tuple[OperationTarget, ...]:
        return self.targets.list()

    def inspect_target(self, target_id: str) -> OperationTarget:
        return self.targets.get(target_id)

    def inspect_target_readiness(
        self, target_id: str, *, probe: bool = False
    ) -> dict[str, object]:
        target = self.targets.get(target_id)
        dependency = _TRANSPORT_DEPENDENCIES.get(target.kind)
        dependency_available = (
            dependency is None or importlib.util.find_spec(dependency) is not None
        )
        result: dict[str, object] = {
            "status": "not_requested",
            "reason_code": "",
            "checked_at": "",
            "target_revision": target.revision,
        }
        if probe:
            result["checked_at"] = utc_now_iso()
            if target.kind != "ssh":
                result.update(status="unsupported", reason_code="unsupported_transport")
            elif not target.enabled:
                result.update(status="failed", reason_code="target_disabled")
            elif not dependency_available:
                result.update(status="failed", reason_code="dependency_missing")
            else:
                transport = self._transports.get("ssh")
                if transport is None:
                    result.update(status="failed", reason_code="unsupported_transport")
                else:
                    try:
                        facts = transport.connect(target)
                    except SshConnectionError as exc:
                        result.update(status="failed", reason_code=exc.reason_code)
                    else:
                        result.update(
                            status="succeeded" if facts.connected else "failed",
                            reason_code="" if facts.connected else "connection_failed",
                        )
        return {
            "dependency_available": dependency_available,
            "transport_ready": dependency_available,
            "probe": result,
        }

    def transport_capabilities(self, target_id: str) -> tuple[str, ...]:
        target = self.targets.get(target_id)
        return tuple(sorted(self._transport_capabilities.get(target.kind, ())))

    def transport_for(self, target_id: str, *, expected_kind: str) -> TargetTransport:
        target = self.targets.get(target_id)
        if target.kind != expected_kind:
            raise ValueError(
                f"target {target_id!r} is not configured for {expected_kind}"
            )
        try:
            return self._transports[target.kind]
        except KeyError as exc:
            raise RuntimeError(
                f"transport unavailable for target kind: {target.kind}"
            ) from exc

    def policy_for(self, request: OperationRequest) -> OperationPolicyDecision:
        return decide_operation_policy(self.targets.get(request.target_id), risk="read")

    def observe(self, request: OperationRequest) -> EvidenceRecord:
        return self._observe(request, operation_id=request.operation_id)

    def _observe(
        self,
        request: OperationRequest,
        *,
        operation_id: str,
    ) -> EvidenceRecord:
        target = self.targets.get(request.target_id)
        if (
            request.expected_target_revision is not None
            and request.expected_target_revision != target.revision
        ):
            raise ValueError("target revision changed")
        decision = decide_operation_policy(target, risk="read")
        if decision.outcome != "allow":
            raise PermissionError(decision.reason)
        transport = self._transports.get(target.kind)
        if transport is None:
            raise RuntimeError(f"transport unavailable for target kind: {target.kind}")
        result = transport.run(
            target,
            build_argv(request, target_platform=target.platform),
            timeout_seconds=min(request.timeout_seconds, target.timeout_seconds),
            operation_id=operation_id,
        )
        return self.evidence.put(
            build_evidence(
                request,
                result,
                target_revision=target.revision,
                transport=target.kind,
                policy_outcome=decision.outcome,
            )
        )

    def submit(self, request: OperationRequest) -> OperationJob:
        target = self.targets.get(request.target_id)
        job = self.jobs.submit(
            request,
            target_revision=target.revision,
            target_limit=target.max_concurrency,
        )
        if job.status != "queued":
            return job
        self.jobs.update(job.job_id, status="running")
        try:
            evidence = self._observe(request, operation_id=job.job_id)
        except (OSError, RuntimeError, ValueError) as exc:
            return self.jobs.update(job.job_id, status="failed", error=str(exc))
        status: JobStatus = (
            "cancelled"
            if evidence.cancelled
            else "succeeded"
            if evidence.claim_status == "observed"
            else "failed"
        )
        return self.jobs.update(
            job.job_id,
            status=status,
            evidence_id=evidence.evidence_id,
            error=evidence.reason if status == "failed" else "",
            remote_outcome="exit_observed"
            if not evidence.timed_out and not evidence.cancelled
            else "unknown",
        )

    def plan_command(
        self,
        *,
        target_id: str,
        argv: tuple[str, ...],
        cwd: str = "",
        timeout_seconds: float = 30.0,
        session_id: str = "",
        idempotency_key: str = "",
        ttl_seconds: int = 300,
    ) -> CommandPlan:
        if not session_id.strip():
            raise ValueError("command plan session is required")
        target = self.targets.get(target_id)
        decision = decide_operation_policy(target, risk="write_safe")
        if decision.outcome == "deny":
            raise PermissionError(decision.reason)
        return self.plans.put(
            build_command_plan(
                target=target,
                argv=argv,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                session_id=session_id,
                idempotency_key=idempotency_key,
                ttl_seconds=ttl_seconds,
                policy_outcome=decision.outcome,
            )
        )

    def run_plan(
        self,
        *,
        plan_id: str,
        plan_hash: str,
        session_id: str,
        subject_id: str = "local",
        agent_id: str = "",
        trace_id: str = "",
        output_sink: Callable[[str, str], None] | None = None,
    ) -> OperationJob:
        plan, target, transport, decision = self._validate_plan_execution(
            plan_id=plan_id,
            plan_hash=plan_hash,
            session_id=session_id,
        )
        request = OperationRequest(
            operation_id=plan.plan_id,
            target_id=plan.target_id,
            profile_id="command.run",
            timeout_seconds=plan.timeout_seconds,
            idempotency_key=plan.idempotency_key or plan.plan_hash,
            expected_target_revision=plan.target_revision,
            session_id=plan.session_id,
            tool_id="ops.command.run",
        )
        job, claim_token = self.jobs.claim_plan_attempt(
            request,
            plan_id=plan.plan_id,
            target_revision=target.revision,
        )
        if not claim_token:
            return job
        policy_decision = self._authorize_plan(
            plan=plan,
            job=job,
            claim_token=claim_token,
            session_id=session_id,
            subject_id=subject_id,
            agent_id=agent_id,
            trace_id=trace_id,
        )
        try:
            self.jobs.reserve_plan_capacity(
                job.job_id,
                claim_token=claim_token,
                target_limit=target.max_concurrency,
            )
            intent = self.jobs.record_dispatch_intent(
                job.job_id,
                claim_token=claim_token,
                approval_id=str(policy_decision.approval_id or ""),
                policy_grant_id=str(policy_decision.matched_grant_id or ""),
                policy_invocation_hash=str(policy_decision.invocation_hash or ""),
            )
        except RuntimeError as exc:
            terminal = self.jobs.finish_plan_attempt(
                job.job_id,
                claim_token=claim_token,
                status="failed",
                error=str(exc),
                remote_outcome="not_dispatched",
            )
            if terminal.status == "cancelled":
                return terminal
            raise
        if intent.status == "cancelled":
            return intent
        try:
            result = transport.run(
                target,
                plan.argv,
                timeout_seconds=plan.timeout_seconds,
                operation_id=job.job_id,
                output_sink=output_sink,
                cwd=plan.cwd,
            )
            evidence = self.evidence.put(
                build_evidence(
                    request,
                    result,
                    redactions=self._redaction_resolver(target),
                    target_revision=target.revision,
                    transport=target.kind,
                    policy_outcome=decision.outcome,
                    approval_id=str(policy_decision.approval_id or ""),
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return self.jobs.finish_plan_attempt(
                job.job_id,
                claim_token=claim_token,
                status="failed",
                error=str(exc),
                remote_outcome="unknown",
            )
        return self._finish_plan_result(job, claim_token, evidence, result)

    def continue_command_approval(
        self, *, approval_id: str, decision: str
    ) -> OperationJob:
        job = self.jobs.find_by_approval_id(approval_id)
        if job is None or not job.plan_id:
            raise KeyError(f"unknown command approval: {approval_id}")
        if decision not in {"allow_once", "deny"}:
            raise ValueError("command approval decision must be allow_once|deny")
        if self.action_policy is None:
            raise ToolRuntimeError(
                "POLICY_DENIED",
                "Operations command execution requires the canonical action policy.",
            )
        if (
            job.status == "cancelled"
            and not job.policy_grant_id
            and decision == "allow_once"
        ):
            raise ValueError("cancelled command approval is no longer runnable")
        awaiting_approval = (
            job.status == "queued" and job.attempt_phase == "awaiting_approval"
        )
        if awaiting_approval:
            plan = self.plans.get(job.plan_id)
            self._validate_plan_execution(
                plan_id=plan.plan_id,
                plan_hash=plan.plan_hash,
                session_id=job.request.session_id,
            )
        self.action_policy.resolve_confirmation(approval_id, decision)
        if decision == "deny":
            return self.jobs.request_cancel(
                job.job_id,
                target_id=job.request.target_id,
                session_id=job.request.session_id,
            )
        if not awaiting_approval:
            return self.jobs.get(job.job_id)
        plan = self.plans.get(job.plan_id)
        return self.run_plan(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            session_id=job.request.session_id,
        )

    def _validate_plan_execution(
        self,
        *,
        plan_id: str,
        plan_hash: str,
        session_id: str,
    ) -> tuple[CommandPlan, OperationTarget, TargetTransport, OperationPolicyDecision]:
        plan = self.plans.get(plan_id)
        validate_command_plan(plan, supplied_hash=plan_hash)
        if not plan.session_id:
            raise PermissionError(
                "legacy command plan must be recreated with a session"
            )
        if not session_id.strip() or session_id != plan.session_id:
            raise PermissionError("command plan belongs to another session")
        target = self.targets.get(plan.target_id)
        if target.revision != plan.target_revision:
            raise ValueError("target revision changed")
        decision = decide_operation_policy(target, risk="write_safe")
        if decision.outcome == "deny":
            raise PermissionError(decision.reason)
        transport = self._transports.get(target.kind)
        if transport is None:
            raise RuntimeError(f"transport unavailable for target kind: {target.kind}")
        return plan, target, transport, decision

    def _finish_plan_result(
        self,
        job: OperationJob,
        claim_token: str,
        evidence: EvidenceRecord,
        result: TransportResult,
    ) -> OperationJob:
        succeeded = (
            not result.timed_out and not result.cancelled and result.return_code == 0
        )
        status: JobStatus
        if result.cancelled:
            status = "cancelled"
        else:
            status = "succeeded" if succeeded else "failed"
        return self.jobs.finish_plan_attempt(
            job.job_id,
            claim_token=claim_token,
            status=status,
            evidence_id=evidence.evidence_id,
            error="" if succeeded else evidence.reason,
            remote_outcome="exit_observed"
            if not result.timed_out and not result.cancelled
            else "unknown",
        )

    def _authorize_plan(
        self,
        *,
        plan: CommandPlan,
        job: OperationJob,
        claim_token: str,
        session_id: str,
        subject_id: str,
        agent_id: str,
        trace_id: str,
    ) -> PolicyDecision:
        if self.action_policy is None:
            self.jobs.finish_plan_attempt(
                job.job_id,
                claim_token=claim_token,
                status="failed",
                error="canonical action policy is unavailable",
                remote_outcome="not_dispatched",
            )
            raise ToolRuntimeError(
                "POLICY_DENIED",
                "Operations command execution requires the canonical action policy.",
                {"reason_code": "POLICY_MODE_UNSUPPORTED"},
            )
        decision = cast(
            PolicyDecision,
            self.action_policy.check(
                {
                    "tool": "ops.command",
                    "method": "run",
                    "args": {"plan_id": plan.plan_id, "plan_hash": plan.plan_hash},
                    "invocation_id": plan.plan_id,
                },
                {
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "agent_id": agent_id,
                    "subject_id": subject_id,
                },
                risk_override=RiskSpec(
                    risk_class="exec",
                    side_effects="remote",
                    reversibility="unknown",
                    default_confirm=True,
                ),
            ),
        )
        if decision.decision == "REQUIRE_CONFIRM":
            self.jobs.await_approval(
                job.job_id,
                claim_token=claim_token,
                approval_id=str(decision.approval_id or ""),
            )
            raise ToolRuntimeError(
                TOOL_ERROR_CONFIRM_REQUIRED,
                decision.reason,
                {
                    "approval_id": str(decision.approval_id or ""),
                    "choices": ["allow_once", "deny"],
                    "job_id": job.job_id,
                    "plan_id": plan.plan_id,
                },
            )
        if (
            decision.decision != "ALLOW"
            or decision.reason_code != "EXACT_PENDING_ALLOW"
            or not decision.approval_id
            or not decision.matched_grant_id
            or decision.invocation_hash is None
        ):
            self.jobs.finish_plan_attempt(
                job.job_id,
                claim_token=claim_token,
                status="failed",
                error=decision.reason,
                remote_outcome="not_dispatched",
            )
            raise ToolRuntimeError("POLICY_DENIED", decision.reason)
        return decision

    def inspect_job(
        self,
        job_id: str,
        *,
        target_id: str = "",
        session_id: str = "",
    ) -> OperationJob:
        job = self.jobs.get(job_id)
        if target_id and job.request.target_id != target_id:
            raise PermissionError("operation job belongs to another target")
        if session_id and job.request.session_id != session_id:
            raise PermissionError("operation job belongs to another session")
        return job

    def cancel_job(
        self,
        job_id: str,
        *,
        target_id: str = "",
        session_id: str = "",
    ) -> OperationJob:
        job = self.inspect_job(
            job_id,
            target_id=target_id,
            session_id=session_id,
        )
        target = self.targets.get(job.request.target_id)
        transport = self._transports.get(target.kind)
        requested = self.jobs.request_cancel(
            job_id,
            target_id=target_id,
            session_id=session_id,
        )
        if requested.status != "running":
            return requested
        delivered = transport.cancel(job_id) if transport is not None else False
        return self.jobs.record_cancel_delivery(job_id, delivered=delivered)

    def mark_job_interrupted(
        self,
        job_id: str,
        *,
        reason: str,
        actor: str,
    ) -> OperationJob:
        job, changed, prior_status, prior_phase = self.jobs.mark_interrupted(
            job_id,
            reason=reason,
            actor=actor,
        )
        if not changed:
            return job
        request = OperationRequest(
            operation_id=job.request.operation_id,
            target_id=job.request.target_id,
            profile_id="job.mark_interrupted",
            parameters={
                "reason": reason,
                "actor": actor,
                "prior_status": prior_status,
                "prior_phase": prior_phase,
            },
            timeout_seconds=1,
            session_id=job.request.session_id,
            tool_id="opsctl.job-mark-interrupted",
        )
        evidence = self.evidence.put(
            build_evidence(
                request,
                TransportResult(
                    argv=("job-mark-interrupted",),
                    return_code=1,
                    stderr="local operator marked the attempt interrupted",
                ),
                target_revision=job.target_revision,
                transport="local_record",
                policy_outcome="operator_confirmed",
                before_facts={"status": prior_status, "phase": prior_phase},
                after_facts={"status": "failed", "remote_outcome": "unknown"},
            )
        )
        return self.jobs.attach_evidence(job_id, evidence.evidence_id)

    def inspect_evidence(self, evidence_id: str) -> EvidenceRecord:
        return self.evidence.get(evidence_id)

    def read_file(
        self,
        *,
        target_id: str,
        path: str,
        max_bytes: int,
        timeout_seconds: float,
        session_id: str = "",
    ) -> EvidenceRecord:
        target = self.targets.get(target_id)
        decision = decide_operation_policy(target, risk="read")
        if decision.outcome != "allow":
            raise PermissionError(decision.reason)
        if "file_read" not in self._transport_capabilities.get(target.kind, ()):
            raise ValueError(f"file read is unsupported for target kind: {target.kind}")
        requested = Path(path)
        if not requested.is_absolute():
            raise ValueError("file read path must be absolute")
        resolved = requested.resolve(strict=False)
        scopes = tuple(
            Path(scope).resolve(strict=False)
            for scope in (*target.workspace_scopes, *target.log_scopes)
        )
        if not scopes or not any(resolved.is_relative_to(scope) for scope in scopes):
            raise ValueError("file read path is outside configured scopes")
        transport = cast(FileReadTransport, self._transports[target.kind])
        result = transport.read(
            target,
            str(resolved),
            max_bytes=max_bytes,
            timeout_seconds=min(timeout_seconds, target.timeout_seconds),
        )
        request = OperationRequest(
            operation_id=f"file-read:{uuid.uuid4()}",
            target_id=target_id,
            profile_id="file.read",
            timeout_seconds=timeout_seconds,
            session_id=session_id,
            tool_id="ops.file.read",
        )
        return self.evidence.put(
            build_evidence(
                request,
                TransportResult(
                    argv=("file.read",),
                    return_code=0,
                    stdout=result.content,
                    truncated=result.truncated,
                ),
                redactions=self._redaction_resolver(target),
                target_revision=target.revision,
                transport=target.kind,
                policy_outcome=decision.outcome,
            )
        )

    def list_evidence(
        self, *, target_id: str = "", session_id: str = ""
    ) -> tuple[EvidenceRecord, ...]:
        return self.evidence.list(target_id=target_id, session_id=session_id)

    def close(self) -> None:
        for transport in self._transports.values():
            transport.close()
        self.jobs.close()
        self.evidence.close()
        self.plans.close()


def local_ops_service() -> OpsService:
    targets = TargetRegistry()
    local_platform: TargetPlatform = (
        "darwin" if platform.system() == "Darwin" else "linux"
    )
    targets.register(
        OperationTarget(target_id="local", kind="local", platform=local_platform)
    )
    return OpsService(targets=targets)


def configured_ops_service(
    config: Mapping[str, object],
    *,
    data_root: Path,
    credential_reader: Callable[[CredentialRef], str] | None = None,
    action_policy: Any | None = None,
) -> OpsService:
    from .registry import registry_from_config

    targets = registry_from_config(config)
    if not targets.list():
        local_platform: TargetPlatform = (
            "darwin" if platform.system() == "Darwin" else "linux"
        )
        targets.register(
            OperationTarget(
                target_id="local",
                kind="local",
                platform=local_platform,
            )
        )
    cache: dict[str, str] = {}

    def read_credential(ref: CredentialRef) -> str:
        key = ref.credential_id
        if key not in cache:
            if credential_reader is None:
                raise RuntimeError("operations credential resolver is unavailable")
            cache[key] = credential_reader(ref)
            if not cache[key]:
                raise RuntimeError("operations credential is unavailable")
        return cache[key]

    transports = build_transports(
        {target.kind for target in targets.list()} | {"local", "container"},
        credential_reader=read_credential,
    )
    storage_root = data_root / "ops"
    storage_root.mkdir(parents=True, exist_ok=True)
    jobs = OperationJobStore(storage_root / "jobs.db")
    return OpsService(
        targets=targets,
        transports=transports,
        jobs=jobs,
        evidence=EvidenceStore(storage_root / "evidence.db"),
        plans=CommandPlanStore(storage_root / "plans.db"),
        redaction_resolver=lambda target: (
            (read_credential(target.credential_ref),)
            if target.credential_ref is not None
            else ()
        ),
        transport_capabilities={
            kind: registration.capabilities
            for kind, registration in registration_map().items()
            if kind in transports
        },
        action_policy=action_policy,
    )
