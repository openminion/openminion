from __future__ import annotations

import platform
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Callable, cast

from openminion.modules.runtime.credentials import CredentialRef

from .evidence import EvidenceStore, build_evidence
from .interfaces import FileReadTransport, TargetTransport
from .jobs import OperationJobStore
from .plans import CommandPlanStore, build_command_plan, validate_command_plan
from .policy import OperationPolicyDecision, decide_operation_policy
from .profiles import build_argv
from .registry import TargetRegistry
from .contracts import (
    EvidenceRecord,
    CommandPlan,
    JobStatus,
    OperationJob,
    OperationRequest,
    OperationTarget,
    TargetPlatform,
    TransportResult,
)
from .transports import build_transports, registration_map

RedactionResolver = Callable[[OperationTarget], tuple[str, ...]]


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
    ) -> None:
        self.targets = targets or TargetRegistry()
        self.jobs = jobs or OperationJobStore()
        self.evidence = evidence or EvidenceStore()
        self.plans = plans or CommandPlanStore()
        self._redaction_resolver = redaction_resolver or (lambda _target: ())
        if transports is None:
            self._transports = build_transports(
                ("local", "container"),
                credential_reader=lambda _ref: "",
            )
            self._transport_capabilities = {
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
            "succeeded" if evidence.claim_status == "observed" else "failed"
        )
        return self.jobs.update(
            job.job_id,
            status=status,
            evidence_id=evidence.evidence_id,
            error=evidence.reason if status == "failed" else "",
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
        approval_id: str,
    ) -> OperationJob:
        if not approval_id.strip():
            raise PermissionError("command plan requires operator approval")
        plan = self.plans.get(plan_id)
        validate_command_plan(plan, supplied_hash=plan_hash)
        target = self.targets.get(plan.target_id)
        if target.revision != plan.target_revision:
            raise ValueError("target revision changed")
        decision = decide_operation_policy(target, risk="write_safe")
        if decision.outcome == "deny":
            raise PermissionError(decision.reason)
        transport = self._transports.get(target.kind)
        if transport is None:
            raise RuntimeError(f"transport unavailable for target kind: {target.kind}")
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
        job = self.jobs.submit(
            request,
            target_revision=target.revision,
            target_limit=target.max_concurrency,
        )
        if job.status != "queued":
            return job
        self.jobs.update(job.job_id, status="running")
        try:
            result = transport.run(
                target,
                plan.argv,
                timeout_seconds=plan.timeout_seconds,
                operation_id=job.job_id,
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
                    approval_id=approval_id,
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return self.jobs.update(job.job_id, status="failed", error=str(exc))
        succeeded = (
            not result.timed_out and not result.cancelled and result.return_code == 0
        )
        return self.jobs.update(
            job.job_id,
            status="succeeded" if succeeded else "failed",
            evidence_id=evidence.evidence_id,
            error="" if succeeded else evidence.reason,
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
        cancelled = self.jobs.cancel(
            job_id,
            target_id=target_id,
            session_id=session_id,
        )
        if transport is not None:
            transport.cancel(job_id)
        return cancelled

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
                policy_outcome="allow",
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
    jobs.recover_running()
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
    )
