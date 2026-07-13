from __future__ import annotations

import os
from pathlib import Path

import pytest

from openminion.modules.runtime.credentials import CredentialRef
from openminion.modules.system_operations.evidence import EvidenceStore
from openminion.modules.system_operations.jobs import OperationJobStore
from openminion.modules.system_operations.registry import TargetRegistry
from openminion.modules.system_operations.schemas import (
    EndpointTrust,
    OperationRequest,
    OperationTarget,
)
from openminion.modules.system_operations.service import SystemOperationsService
from openminion.modules.system_operations.transports import SshTransport


@pytest.mark.e2e
def test_live_ssh_readonly_smoke(tmp_path: Path) -> None:
    if os.getenv("OPENMINION_LIVE_OPS_SSH") != "1":
        pytest.skip("set OPENMINION_LIVE_OPS_SSH=1 for the opt-in SSH smoke")
    required = {
        name: os.getenv(name, "")
        for name in (
            "OPENMINION_OPS_SSH_HOST",
            "OPENMINION_OPS_SSH_USER",
            "OPENMINION_OPS_SSH_HOST_KEY",
            "OPENMINION_OPS_SSH_PASSWORD",
        )
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        pytest.fail(f"missing live SSH settings: {', '.join(missing)}")

    target = OperationTarget(
        target_id="live-ssh",
        kind="ssh",
        address=required["OPENMINION_OPS_SSH_HOST"],
        username=required["OPENMINION_OPS_SSH_USER"],
        credential_ref=CredentialRef(
            credential_id="live-ssh",
            scope_kind="tool_family",
            scope_id="system_operations",
            source_kind="env",
            env_name="OPENMINION_OPS_SSH_PASSWORD",
            rotation_policy="static",
        ),
        endpoint_trust=EndpointTrust(host_key=required["OPENMINION_OPS_SSH_HOST_KEY"]),
    )

    targets = TargetRegistry()
    targets.register(target)
    jobs_path = tmp_path / "jobs.db"
    evidence_path = tmp_path / "evidence.db"
    service = SystemOperationsService(
        targets=targets,
        transports={
            "ssh": SshTransport(
                lambda _: required["OPENMINION_OPS_SSH_PASSWORD"]
            )
        },
        jobs=OperationJobStore(jobs_path),
        evidence=EvidenceStore(evidence_path),
    )
    job = service.submit(
        OperationRequest(
            operation_id="live-ssh-readonly",
            target_id=target.target_id,
            expected_target_revision=target.revision,
            profile_id="host.snapshot",
            session_id="live-ssh-smoke",
            tool_id="ops.host.snapshot",
            idempotency_key="live-ssh-readonly",
            timeout_seconds=15,
        )
    )

    assert job.status == "succeeded"
    assert job.target_revision == target.revision
    assert job.expires_at
    evidence = EvidenceStore(evidence_path).get(job.evidence_id)
    assert evidence.claim_status == "observed"
    assert evidence.target_id == target.target_id
    assert evidence.target_revision == target.revision
    assert evidence.transport == "ssh"
    assert evidence.stdout_preview.strip()
