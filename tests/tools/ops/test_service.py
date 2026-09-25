import threading
from datetime import datetime, timedelta

import pytest

from openminion.modules.policy.models import PolicyConfig
from openminion.modules.policy.runtime.service import PolicyCtl
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.ops.evidence import EvidenceStore
from openminion.tools.ops.jobs import OperationJobStore
from openminion.tools.ops.plans import CommandPlanStore
from openminion.tools.ops.registry import TargetRegistry
from openminion.tools.ops.contracts import (
    OperationRequest,
    OperationTarget,
    TransportResult,
)
from openminion.tools.ops.transports import LocalTransport
from openminion.tools.ops.service import (
    OpsService,
    configured_ops_service,
    local_ops_service,
)


class _RecordingTransport:
    def __init__(self, *, block: bool = False) -> None:
        self.block = block
        self.calls = 0
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.timeout_seconds = 0.0
        self.operation_id = ""

    def run(
        self,
        target: OperationTarget,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        operation_id: str = "",
        output_sink: object = None,
        cwd: str = "",
    ) -> TransportResult:
        del target, output_sink, cwd
        self.calls += 1
        self.timeout_seconds = timeout_seconds
        self.operation_id = operation_id
        self.started.set()
        if self.block:
            self.cancelled.wait(timeout=2)
        return TransportResult(
            argv=argv,
            return_code=130 if self.cancelled.is_set() else 0,
            stdout="observed" if not self.cancelled.is_set() else "",
            cancelled=self.cancelled.is_set(),
        )

    def cancel(self, operation_id: str) -> bool:
        if operation_id != self.operation_id:
            return False
        self.cancelled.set()
        return True


def _action_policy() -> PolicyCtl:
    return PolicyCtl.with_sqlite(":memory:", config=PolicyConfig(mode="enforce"))


def _approve_and_run(service: OpsService, plan) -> object:
    with pytest.raises(ToolRuntimeError, match="confirmation") as pending:
        service.run_plan(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            session_id=plan.session_id,
        )
    approval_id = str(pending.value.details["approval_id"])
    service.action_policy.resolve_confirmation(approval_id, "allow_once")
    return service.run_plan(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        session_id=plan.session_id,
    )


def _pending_approval(service: OpsService, plan) -> str:
    with pytest.raises(ToolRuntimeError, match="confirmation") as pending:
        service.run_plan(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            session_id=plan.session_id,
        )
    return str(pending.value.details["approval_id"])


def _request(**overrides: object) -> OperationRequest:
    values: dict[str, object] = {
        "operation_id": "observe-1",
        "target_id": "local",
        "profile_id": "host.snapshot",
        "expected_target_revision": 1,
    }
    values.update(overrides)
    return OperationRequest.model_validate(values)


def test_service_observes_closed_profile() -> None:
    evidence = local_ops_service().observe(_request())
    assert evidence.claim_status == "observed"
    assert evidence.output_digest


def test_file_read_is_bounded_to_configured_scopes(tmp_path) -> None:
    allowed = tmp_path / "workspace"
    allowed.mkdir()
    source = allowed / "status.txt"
    source.write_text("visible-secret", encoding="utf-8")
    service = OpsService(
        targets=TargetRegistry(
            (
                OperationTarget(
                    target_id="local",
                    kind="local",
                    workspace_scopes=(str(allowed),),
                ),
            )
        ),
        transports={"local": LocalTransport()},
        transport_capabilities={"local": frozenset({"command", "file_read"})},
        redaction_resolver=lambda _target: ("secret",),
    )

    evidence = service.read_file(
        target_id="local",
        path=str(source),
        max_bytes=8,
        timeout_seconds=2,
    )

    assert evidence.stdout_preview == "visible-"
    assert evidence.claim_status == "observed"
    with pytest.raises(ValueError, match="must be absolute"):
        service.read_file(
            target_id="local",
            path="status.txt",
            max_bytes=10,
            timeout_seconds=2,
        )
    with pytest.raises(ValueError, match="outside configured scopes"):
        service.read_file(
            target_id="local",
            path=str(tmp_path / "outside.txt"),
            max_bytes=10,
            timeout_seconds=2,
        )


def test_file_read_rejects_command_only_transport_before_dispatch(tmp_path) -> None:
    service = OpsService(
        targets=TargetRegistry(
            (
                OperationTarget(
                    target_id="local",
                    kind="local",
                    workspace_scopes=(str(tmp_path),),
                ),
            )
        ),
        transports={"local": _RecordingTransport()},
        transport_capabilities={"local": frozenset({"command"})},
    )

    with pytest.raises(ValueError, match="unsupported"):
        service.read_file(
            target_id="local",
            path=str(tmp_path / "status.txt"),
            max_bytes=10,
            timeout_seconds=2,
        )


def test_file_read_rejects_disabled_target_before_transport_dispatch(tmp_path) -> None:
    transport = _RecordingTransport()
    service = OpsService(
        targets=TargetRegistry(
            (
                OperationTarget(
                    target_id="disabled",
                    kind="local",
                    enabled=False,
                    workspace_scopes=(str(tmp_path),),
                ),
            )
        ),
        transports={"local": transport},
        transport_capabilities={"local": frozenset({"command", "file_read"})},
    )

    with pytest.raises(PermissionError, match="disabled"):
        service.read_file(
            target_id="disabled",
            path=str(tmp_path / "status.txt"),
            max_bytes=10,
            timeout_seconds=2,
        )
    assert transport.started.is_set() is False


def test_service_rejects_stale_target_and_unknown_profile() -> None:
    service = local_ops_service()
    with pytest.raises(ValueError, match="target revision changed"):
        service.observe(_request(expected_target_revision=2))
    with pytest.raises(ValueError, match="unknown operation profile"):
        service.observe(_request(profile_id="shell.anything"))


def test_jobs_are_idempotent_and_cancellable() -> None:
    service = local_ops_service()
    request = _request(idempotency_key="same-observation")
    first = service.submit(request)
    second = service.submit(request)
    assert first.job_id == second.job_id
    assert first.status == "succeeded"

    pending = service.jobs.submit(_request(operation_id="queued"), target_revision=1)
    assert service.cancel_job(pending.job_id).status == "cancelled"


def test_service_clamps_timeout_to_target_limit() -> None:
    transport = _RecordingTransport()
    targets = TargetRegistry(
        (OperationTarget(target_id="bounded", kind="local", timeout_seconds=2),)
    )
    service = OpsService(
        targets=targets,
        transports={"local": transport},
    )

    evidence = service.observe(
        _request(
            operation_id="bounded-observation",
            target_id="bounded",
            timeout_seconds=10,
        )
    )

    assert evidence.claim_status == "observed"
    assert transport.timeout_seconds == 2
    assert transport.operation_id == "bounded-observation"


def test_job_cancellation_reaches_active_transport() -> None:
    transport = _RecordingTransport(block=True)
    service = OpsService(
        targets=TargetRegistry((OperationTarget(target_id="local", kind="local"),)),
        transports={"local": transport},
    )
    result: dict[str, object] = {}

    def submit() -> None:
        result["job"] = service.submit(_request(session_id="session-1"))

    thread = threading.Thread(target=submit)
    thread.start()
    assert transport.started.wait(timeout=1)
    running = service.jobs.list()[0]

    cancelled = service.cancel_job(
        running.job_id,
        target_id="local",
        session_id="session-1",
    )
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert transport.operation_id == running.job_id
    assert transport.cancelled.is_set()
    assert cancelled.status == "running"
    assert cancelled.cancel_requested is True
    assert cancelled.cancel_status == "cancel_delivered"
    completed = service.inspect_job(running.job_id)
    assert completed.status == "cancelled"
    assert completed.remote_outcome == "unknown"


def test_command_plan_run_is_hash_bound_and_records_evidence() -> None:
    transport = _RecordingTransport()
    service = OpsService(
        targets=TargetRegistry(
            (
                OperationTarget(
                    target_id="staging",
                    kind="local",
                    environment="staging",
                    workspace_scopes=("/srv/app",),
                ),
            )
        ),
        transports={"local": transport},
        action_policy=_action_policy(),
    )
    plan = service.plan_command(
        target_id="staging",
        argv=("printf", "%s", "hello"),
        cwd="/srv/app/releases",
        timeout_seconds=15,
        session_id="session-1",
    )

    with pytest.raises(ValueError, match="hash changed"):
        service.run_plan(
            plan_id=plan.plan_id,
            plan_hash="0" * 64,
            session_id="session-1",
        )
    job = _approve_and_run(service, plan)

    assert job.status == "succeeded"
    evidence = service.inspect_evidence(job.evidence_id)
    assert evidence.approval_id
    assert evidence.command_hash
    assert evidence.target_revision == 1


@pytest.mark.parametrize(
    "argv, error",
    [
        (("sh", "-c", "uname -a"), "shell executables"),
        (("sudo", "uname", "-a"), "privileged"),
        (("shutdown", "-h", "now"), "dangerous"),
        (("rm", "-rf", "/tmp/example"), "dangerous"),
        ((), "cannot be empty"),
    ],
)
def test_command_plan_rejects_unsafe_argv(argv: tuple[str, ...], error: str) -> None:
    service = OpsService(
        targets=TargetRegistry((OperationTarget(target_id="staging", kind="local"),))
    )

    with pytest.raises((ValueError, PermissionError), match=error):
        service.plan_command(target_id="staging", argv=argv, session_id="session-1")


def test_command_plan_denies_production_and_cwd_escape() -> None:
    service = OpsService(
        targets=TargetRegistry(
            (
                OperationTarget(
                    target_id="staging",
                    kind="local",
                    workspace_scopes=("/srv/app",),
                ),
                OperationTarget(
                    target_id="production",
                    kind="local",
                    environment="production",
                ),
            )
        )
    )

    with pytest.raises(ValueError, match="outside configured workspace"):
        service.plan_command(
            target_id="staging",
            argv=("uname", "-a"),
            cwd="/tmp",
            session_id="session-1",
        )
    with pytest.raises(ValueError, match="inside a configured workspace"):
        service.plan_command(
            target_id="staging",
            argv=("uname", "-a"),
            cwd="/srv/app/../../tmp",
            session_id="session-1",
        )
    with pytest.raises(PermissionError, match="production"):
        service.plan_command(
            target_id="production", argv=("uname", "-a"), session_id="session-1"
        )


def test_configured_service_persists_plans_jobs_and_evidence(tmp_path) -> None:
    config = {"targets": [{"target_id": "staging", "kind": "local"}]}
    service = configured_ops_service(
        config, data_root=tmp_path, action_policy=_action_policy()
    )
    plan = service.plan_command(
        target_id="staging",
        argv=("printf", "ready"),
        session_id="session-1",
    )
    job = _approve_and_run(service, plan)
    evidence_id = job.evidence_id
    service.close()

    reopened = configured_ops_service(
        config, data_root=tmp_path, action_policy=_action_policy()
    )

    assert reopened.plans.get(plan.plan_id) == plan
    assert reopened.inspect_job(job.job_id).status == "succeeded"
    assert reopened.inspect_evidence(evidence_id).stdout_preview == "ready"
    reopened.close()


def test_configured_service_does_not_relabel_running_job_on_open(tmp_path) -> None:
    config = {"targets": [{"target_id": "staging", "kind": "local"}]}
    service = configured_ops_service(config, data_root=tmp_path)
    job = service.jobs.submit(_request(session_id="session-1"), target_revision=1)
    service.jobs.update(job.job_id, status="running")
    service.close()

    reopened = configured_ops_service(config, data_root=tmp_path)

    assert reopened.inspect_job(job.job_id).status == "running"
    reopened.close()


def test_second_process_inspection_does_not_relabel_active_job(tmp_path) -> None:
    transport = _RecordingTransport(block=True)
    target = OperationTarget(target_id="local", kind="local")
    first = OpsService(
        targets=TargetRegistry((target,)),
        transports={"local": transport},
        jobs=OperationJobStore(tmp_path / "jobs.db"),
    )
    result = {}

    def run() -> None:
        result["job"] = first.submit(_request(session_id="session-1"))

    thread = threading.Thread(target=run)
    thread.start()
    assert transport.started.wait(timeout=1)
    running = first.jobs.list()[0]
    second = OpsService(
        targets=TargetRegistry((target,)),
        transports={},
        jobs=OperationJobStore(tmp_path / "jobs.db"),
    )

    assert second.inspect_job(running.job_id).status == "running"
    assert first.inspect_job(running.job_id).status == "running"

    first.cancel_job(running.job_id)
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result["job"].status == "cancelled"


def test_mark_job_interrupted_records_local_evidence_without_transport() -> None:
    transport = _RecordingTransport()
    service = OpsService(
        targets=TargetRegistry((OperationTarget(target_id="local", kind="local"),)),
        transports={"local": transport},
    )
    job = service.jobs.submit(_request(session_id="session-1"), target_revision=1)
    service.jobs.update(job.job_id, status="running")

    marked = service.mark_job_interrupted(
        job.job_id,
        reason="remote state checked separately",
        actor="local",
    )
    evidence = service.inspect_evidence(marked.evidence_id)

    assert marked.status == "failed"
    assert marked.remote_outcome == "unknown"
    assert transport.started.is_set() is False
    assert evidence.transport == "local_record"
    assert evidence.before_facts == {"status": "running", "phase": ""}
    assert evidence.after_facts == {"status": "failed", "remote_outcome": "unknown"}


def test_command_plan_rechecks_expiry_and_target_revision() -> None:
    targets = TargetRegistry((OperationTarget(target_id="staging", kind="local"),))
    service = OpsService(targets=targets)
    expired = service.plan_command(
        target_id="staging",
        argv=("printf", "ready"),
        ttl_seconds=-1,
        session_id="session-1",
    )

    with pytest.raises(ValueError, match="expired"):
        service.run_plan(
            plan_id=expired.plan_id,
            plan_hash=expired.plan_hash,
            session_id="session-1",
        )
    current = service.plan_command(
        target_id="staging", argv=("printf", "ready"), session_id="session-1"
    )
    targets.register(OperationTarget(target_id="staging", kind="local", revision=2))
    with pytest.raises(ValueError, match="target revision changed"):
        service.run_plan(
            plan_id=current.plan_id,
            plan_hash=current.plan_hash,
            session_id="session-1",
        )


def test_command_evidence_redacts_configured_literals() -> None:
    class SecretTransport(_RecordingTransport):
        def run(self, *args: object, **kwargs: object) -> TransportResult:
            result = super().run(*args, **kwargs)
            return result.model_copy(update={"stdout": "token=secret-value"})

    service = OpsService(
        targets=TargetRegistry((OperationTarget(target_id="staging", kind="local"),)),
        transports={"local": SecretTransport()},
        redaction_resolver=lambda _target: ("secret-value",),
        action_policy=_action_policy(),
    )
    plan = service.plan_command(
        target_id="staging", argv=("printf", "ready"), session_id="session-1"
    )
    job = _approve_and_run(service, plan)

    evidence = service.inspect_evidence(job.evidence_id)
    assert evidence.stdout_preview == "token=[REDACTED]"
    assert "secret-value" not in evidence.model_dump_json()


def test_two_service_processes_dispatch_one_plan_once(tmp_path) -> None:
    target = OperationTarget(target_id="staging", kind="local")
    targets = TargetRegistry((target,))
    transport = _RecordingTransport()
    paths = {
        "jobs": tmp_path / "jobs.db",
        "evidence": tmp_path / "evidence.db",
        "plans": tmp_path / "plans.db",
        "policy": tmp_path / "policy.db",
    }

    def open_service() -> OpsService:
        return OpsService(
            targets=targets,
            transports={"local": transport},
            jobs=OperationJobStore(paths["jobs"]),
            evidence=EvidenceStore(paths["evidence"]),
            plans=CommandPlanStore(paths["plans"]),
            action_policy=PolicyCtl.with_sqlite(
                paths["policy"], config=PolicyConfig(mode="enforce")
            ),
        )

    first = open_service()
    plan = first.plan_command(
        target_id="staging", argv=("printf", "ready"), session_id="session-1"
    )
    with pytest.raises(ToolRuntimeError) as pending:
        first.run_plan(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            session_id=plan.session_id,
        )
    first.action_policy.resolve_confirmation(
        str(pending.value.details["approval_id"]), "allow_once"
    )
    second = open_service()
    jobs = []

    def run(service: OpsService) -> None:
        jobs.append(
            service.run_plan(
                plan_id=plan.plan_id,
                plan_hash=plan.plan_hash,
                session_id=plan.session_id,
            )
        )

    threads = [
        threading.Thread(target=run, args=(first,)),
        threading.Thread(target=run, args=(second,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert len(jobs) == 2
    assert len({job.job_id for job in jobs}) == 1
    assert transport.calls == 1
    assert transport.operation_id == jobs[0].job_id
    assert first.inspect_job(jobs[0].job_id).status == "succeeded"


def test_command_approval_continuation_runs_once() -> None:
    transport = _RecordingTransport()
    service = OpsService(
        targets=TargetRegistry((OperationTarget(target_id="staging", kind="local"),)),
        transports={"local": transport},
        action_policy=_action_policy(),
    )
    plan = service.plan_command(
        target_id="staging", argv=("printf", "ready"), session_id="session-1"
    )
    approval_id = _pending_approval(service, plan)

    completed = service.continue_command_approval(
        approval_id=approval_id,
        decision="allow_once",
    )
    repeated = service.continue_command_approval(
        approval_id=approval_id,
        decision="allow_once",
    )

    assert completed.status == "succeeded"
    assert repeated.job_id == completed.job_id
    assert transport.calls == 1


def test_command_approval_denial_never_dispatches() -> None:
    transport = _RecordingTransport()
    service = OpsService(
        targets=TargetRegistry((OperationTarget(target_id="staging", kind="local"),)),
        transports={"local": transport},
        action_policy=_action_policy(),
    )
    plan = service.plan_command(
        target_id="staging", argv=("printf", "ready"), session_id="session-1"
    )
    approval_id = _pending_approval(service, plan)

    denied = service.continue_command_approval(
        approval_id=approval_id,
        decision="deny",
    )

    assert denied.status == "cancelled"
    assert denied.remote_outcome == "not_dispatched"
    assert transport.calls == 0


def test_cancelled_command_rejects_late_allow_without_creating_grant() -> None:
    transport = _RecordingTransport()
    service = OpsService(
        targets=TargetRegistry((OperationTarget(target_id="staging", kind="local"),)),
        transports={"local": transport},
        action_policy=_action_policy(),
    )
    plan = service.plan_command(
        target_id="staging", argv=("printf", "ready"), session_id="session-1"
    )
    approval_id = _pending_approval(service, plan)
    job = service.jobs.find_by_approval_id(approval_id)
    assert job is not None
    service.jobs.request_cancel(
        job.job_id,
        target_id=job.request.target_id,
        session_id=job.request.session_id,
    )

    with pytest.raises(ValueError, match="no longer runnable"):
        service.continue_command_approval(
            approval_id=approval_id,
            decision="allow_once",
        )

    assert service.action_policy.list_grants(active_only=True) == []
    repeated_deny = service.continue_command_approval(
        approval_id=approval_id,
        decision="deny",
    )
    assert repeated_deny.status == "cancelled"
    assert transport.calls == 0


def test_running_command_retry_does_not_revalidate_expired_plan(monkeypatch) -> None:
    transport = _RecordingTransport(block=True)
    service = OpsService(
        targets=TargetRegistry((OperationTarget(target_id="staging", kind="local"),)),
        transports={"local": transport},
        action_policy=_action_policy(),
    )
    plan = service.plan_command(
        target_id="staging",
        argv=("printf", "ready"),
        session_id="session-1",
        ttl_seconds=1,
    )
    approval_id = _pending_approval(service, plan)
    completed = []

    thread = threading.Thread(
        target=lambda: completed.append(
            service.continue_command_approval(
                approval_id=approval_id,
                decision="allow_once",
            )
        )
    )
    thread.start()
    assert transport.started.wait(timeout=1)
    expired_at = datetime.fromisoformat(plan.expires_at) + timedelta(seconds=1)
    monkeypatch.setattr("openminion.tools.ops.plans.utc_now", lambda: expired_at)

    repeated = service.continue_command_approval(
        approval_id=approval_id,
        decision="allow_once",
    )
    transport.cancelled.set()
    thread.join(timeout=2)

    assert repeated.status == "running"
    assert len(completed) == 1
    assert transport.calls == 1


def test_command_approval_continuation_survives_reopen(tmp_path) -> None:
    config = {"targets": [{"target_id": "staging", "kind": "local"}]}
    policy_path = tmp_path / "policy.db"
    service = configured_ops_service(
        config,
        data_root=tmp_path,
        action_policy=PolicyCtl.with_sqlite(
            policy_path, config=PolicyConfig(mode="enforce")
        ),
    )
    plan = service.plan_command(
        target_id="staging", argv=("printf", "ready"), session_id="session-1"
    )
    approval_id = _pending_approval(service, plan)
    service.close()

    reopened = configured_ops_service(
        config,
        data_root=tmp_path,
        action_policy=PolicyCtl.with_sqlite(
            policy_path, config=PolicyConfig(mode="enforce")
        ),
    )
    completed = reopened.continue_command_approval(
        approval_id=approval_id,
        decision="allow_once",
    )

    assert completed.status == "succeeded"
    assert completed.plan_id == plan.plan_id


def test_consumed_approval_is_not_restored_after_capacity_failure() -> None:
    target = OperationTarget(target_id="staging", kind="local", max_concurrency=1)
    transport = _RecordingTransport()
    service = OpsService(
        targets=TargetRegistry((target,)),
        transports={"local": transport},
        action_policy=_action_policy(),
    )
    active = service.jobs.submit(
        _request(
            operation_id="active",
            target_id="staging",
            session_id="other-session",
        ),
        target_revision=1,
    )
    service.jobs.update(active.job_id, status="running")
    plan = service.plan_command(
        target_id="staging", argv=("printf", "ready"), session_id="session-1"
    )
    with pytest.raises(ToolRuntimeError) as pending:
        service.run_plan(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            session_id=plan.session_id,
        )
    service.action_policy.resolve_confirmation(
        str(pending.value.details["approval_id"]), "allow_once"
    )

    with pytest.raises(RuntimeError, match="concurrency limit"):
        service.run_plan(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            session_id=plan.session_id,
        )
    recorded = service.run_plan(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        session_id=plan.session_id,
    )

    assert recorded.status == "failed"
    assert recorded.remote_outcome == "not_dispatched"
    assert transport.started.is_set() is False


def test_wrong_session_does_not_consume_pending_plan_approval() -> None:
    service = OpsService(
        targets=TargetRegistry((OperationTarget(target_id="staging", kind="local"),)),
        transports={"local": _RecordingTransport()},
        action_policy=_action_policy(),
    )
    plan = service.plan_command(
        target_id="staging", argv=("printf", "ready"), session_id="session-1"
    )
    with pytest.raises(ToolRuntimeError) as pending:
        service.run_plan(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            session_id=plan.session_id,
        )
    service.action_policy.resolve_confirmation(
        str(pending.value.details["approval_id"]), "allow_once"
    )

    with pytest.raises(PermissionError, match="another session"):
        service.run_plan(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            session_id="wrong-session",
        )
    completed = service.run_plan(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        session_id=plan.session_id,
    )

    assert completed.status == "succeeded"
