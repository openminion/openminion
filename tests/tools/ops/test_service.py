import threading

import pytest

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
    assert cancelled.status == "cancelled"
    assert service.inspect_job(running.job_id).status == "cancelled"


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
    )
    plan = service.plan_command(
        target_id="staging",
        argv=("printf", "%s", "hello"),
        cwd="/srv/app/releases",
        session_id="session-1",
    )

    with pytest.raises(ValueError, match="hash changed"):
        service.run_plan(
            plan_id=plan.plan_id,
            plan_hash="0" * 64,
            approval_id="approval-1",
        )
    job = service.run_plan(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        approval_id="approval-1",
    )

    assert job.status == "succeeded"
    evidence = service.inspect_evidence(job.evidence_id)
    assert evidence.approval_id == "approval-1"
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
        service.plan_command(target_id="staging", argv=argv)


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
        )
    with pytest.raises(ValueError, match="inside a configured workspace"):
        service.plan_command(
            target_id="staging",
            argv=("uname", "-a"),
            cwd="/srv/app/../../tmp",
        )
    with pytest.raises(PermissionError, match="production"):
        service.plan_command(target_id="production", argv=("uname", "-a"))


def test_configured_service_persists_plans_jobs_and_evidence(tmp_path) -> None:
    config = {"targets": [{"target_id": "staging", "kind": "local"}]}
    service = configured_ops_service(config, data_root=tmp_path)
    plan = service.plan_command(target_id="staging", argv=("printf", "ready"))
    job = service.run_plan(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        approval_id="approval-1",
    )
    evidence_id = job.evidence_id
    service.close()

    reopened = configured_ops_service(config, data_root=tmp_path)

    assert reopened.plans.get(plan.plan_id) == plan
    assert reopened.inspect_job(job.job_id).status == "succeeded"
    assert reopened.inspect_evidence(evidence_id).stdout_preview == "ready"
    reopened.close()


def test_command_plan_rechecks_expiry_and_target_revision() -> None:
    targets = TargetRegistry((OperationTarget(target_id="staging", kind="local"),))
    service = OpsService(targets=targets)
    expired = service.plan_command(
        target_id="staging",
        argv=("printf", "ready"),
        ttl_seconds=-1,
    )

    with pytest.raises(ValueError, match="expired"):
        service.run_plan(
            plan_id=expired.plan_id,
            plan_hash=expired.plan_hash,
            approval_id="approval-1",
        )
    current = service.plan_command(target_id="staging", argv=("printf", "ready"))
    targets.register(OperationTarget(target_id="staging", kind="local", revision=2))
    with pytest.raises(ValueError, match="target revision changed"):
        service.run_plan(
            plan_id=current.plan_id,
            plan_hash=current.plan_hash,
            approval_id="approval-2",
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
    )
    plan = service.plan_command(target_id="staging", argv=("printf", "ready"))
    job = service.run_plan(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        approval_id="approval-1",
    )

    evidence = service.inspect_evidence(job.evidence_id)
    assert evidence.stdout_preview == "token=[REDACTED]"
    assert "secret-value" not in evidence.model_dump_json()
