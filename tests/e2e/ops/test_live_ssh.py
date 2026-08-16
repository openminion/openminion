from __future__ import annotations

import os
from pathlib import Path

import pytest

from openminion.modules.runtime.credentials import CredentialRef
from openminion.tools.ops.evidence import EvidenceStore
from openminion.tools.ops.jobs import OperationJobStore
from openminion.tools.ops.registry import TargetRegistry
from openminion.tools.ops.contracts import (
    EndpointTrust,
    OperationRequest,
    OperationTarget,
)
from openminion.tools.ops.service import OpsService
from openminion.tools.ops.transports import SshTransport


@pytest.mark.e2e
def test_live_ssh_readonly_smoke(tmp_path: Path) -> None:
    if os.getenv("OPENMINION_LIVE_OPS_SSH") != "1":
        pytest.skip("set OPENMINION_LIVE_OPS_SSH=1 for the opt-in SSH smoke")
    auth_mode = os.getenv("OPENMINION_OPS_SSH_AUTH_MODE", "password")
    credential_env = (
        "OPENMINION_OPS_SSH_PRIVATE_KEY"
        if auth_mode == "private_key"
        else "OPENMINION_OPS_SSH_PASSWORD"
    )
    required = {
        name: os.getenv(name, "")
        for name in (
            "OPENMINION_OPS_SSH_HOST",
            "OPENMINION_OPS_SSH_USER",
            "OPENMINION_OPS_SSH_HOST_KEY",
            credential_env,
        )
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        pytest.fail(f"missing live SSH settings: {', '.join(missing)}")

    target = OperationTarget(
        target_id="live-ssh",
        kind="ssh",
        address=required["OPENMINION_OPS_SSH_HOST"],
        port=int(os.getenv("OPENMINION_OPS_SSH_PORT", "22")),
        username=required["OPENMINION_OPS_SSH_USER"],
        ssh_auth_mode=auth_mode,
        credential_ref=CredentialRef(
            credential_id="live-ssh",
            scope_kind="tool_family",
            scope_id="ops",
            source_kind="env",
            env_name=credential_env,
            rotation_policy="static",
        ),
        endpoint_trust=EndpointTrust(host_key=required["OPENMINION_OPS_SSH_HOST_KEY"]),
    )

    targets = TargetRegistry()
    targets.register(target)
    jobs_path = tmp_path / "jobs.db"
    evidence_path = tmp_path / "evidence.db"
    service = OpsService(
        targets=targets,
        transports={"ssh": SshTransport(lambda _: required[credential_env])},
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

    plan = service.plan_command(
        target_id=target.target_id,
        argv=("uname", "-a"),
        timeout_seconds=15,
        session_id="live-ssh-smoke",
    )
    command_job = service.run_plan(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        approval_id="live-ssh-explicit-approval",
    )
    command_evidence = service.inspect_evidence(command_job.evidence_id)
    assert command_job.status == "succeeded"
    assert command_evidence.approval_id == "live-ssh-explicit-approval"
    assert command_evidence.target_revision == target.revision
    assert command_evidence.stdout_preview.strip()
