import threading

import pytest

from openminion.modules.capability_pack.resolver import activate_pack
from openminion.modules.system_operations.manifest import (
    READ_ONLY_TOOLS,
    read_only_manifest,
)
from openminion.modules.system_operations.registry import TargetRegistry
from openminion.modules.system_operations.schemas import (
    OperationRequest,
    OperationTarget,
    TransportResult,
)
from openminion.modules.system_operations.service import (
    SystemOperationsService,
    local_operations_service,
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
    ) -> TransportResult:
        del target, output_sink
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
    evidence = local_operations_service().observe(_request())
    assert evidence.claim_status == "observed"
    assert evidence.output_digest


def test_service_rejects_stale_target_and_unknown_profile() -> None:
    service = local_operations_service()
    with pytest.raises(ValueError, match="target revision changed"):
        service.observe(_request(expected_target_revision=2))
    with pytest.raises(ValueError, match="unknown operation profile"):
        service.observe(_request(profile_id="shell.anything"))


def test_jobs_are_idempotent_and_cancellable() -> None:
    service = local_operations_service()
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
    service = SystemOperationsService(
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
    service = SystemOperationsService(
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


def test_read_only_manifest_narrows_tools_and_is_domain_neutral() -> None:
    manifest = read_only_manifest()
    active = activate_pack(
        manifest,
        session_id="session-1",
        available_tools=READ_ONLY_TOOLS,
        available_skills=("ops-linux-diagnostics", "ops-incident-handoff"),
    )
    assert active.visible_tools == tuple(sorted(READ_ONLY_TOOLS))
    assert active.pack_id == "ops-linux-readonly"
