from __future__ import annotations

import platform
from collections.abc import Mapping

from .evidence import EvidenceStore, build_evidence
from .interfaces import TargetTransport
from .jobs import OperationJobStore
from .policy import OperationPolicyDecision, decide_operation_policy
from .profiles import build_argv
from .registry import TargetRegistry
from .schemas import (
    EvidenceRecord,
    JobStatus,
    OperationJob,
    OperationRequest,
    OperationTarget,
    TargetPlatform,
)
from .transports import ContainerTransport, LocalTransport


class SystemOperationsService:
    def __init__(
        self,
        *,
        targets: TargetRegistry | None = None,
        transports: Mapping[str, TargetTransport] | None = None,
        jobs: OperationJobStore | None = None,
        evidence: EvidenceStore | None = None,
    ) -> None:
        self.targets = targets or TargetRegistry()
        self.jobs = jobs or OperationJobStore()
        self.evidence = evidence or EvidenceStore()
        if transports is None:
            self._transports: dict[str, TargetTransport] = {
                "local": LocalTransport(),
                "container": ContainerTransport(),
            }
        else:
            self._transports = dict(transports)

    def list_targets(self) -> tuple[OperationTarget, ...]:
        return self.targets.list()

    def inspect_target(self, target_id: str) -> OperationTarget:
        return self.targets.get(target_id)

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
        job = self.jobs.submit(request, target_revision=target.revision)
        if job.status != "queued":
            return job
        self.jobs.update(job.job_id, status="running")
        try:
            evidence = self._observe(request, operation_id=job.job_id)
        except Exception as exc:
            return self.jobs.update(job.job_id, status="failed", error=str(exc))
        status: JobStatus = (
            "succeeded" if evidence.claim_status == "observed" else "failed"
        )
        return self.jobs.update(
            job.job_id,
            status=status,
            evidence_id=evidence.evidence_id,
            error=evidence.reason if status == "failed" else "",
        )

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
        if transport is not None:
            transport.cancel(job_id)
        return self.jobs.cancel(
            job_id,
            target_id=target_id,
            session_id=session_id,
        )

    def inspect_evidence(self, evidence_id: str) -> EvidenceRecord:
        return self.evidence.get(evidence_id)

    def list_evidence(
        self, *, target_id: str = "", session_id: str = ""
    ) -> tuple[EvidenceRecord, ...]:
        return self.evidence.list(target_id=target_id, session_id=session_id)


def local_operations_service() -> SystemOperationsService:
    targets = TargetRegistry()
    local_platform: TargetPlatform = (
        "darwin" if platform.system() == "Darwin" else "linux"
    )
    targets.register(
        OperationTarget(target_id="local", kind="local", platform=local_platform)
    )
    return SystemOperationsService(targets=targets)
