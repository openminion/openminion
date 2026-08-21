from __future__ import annotations

from typing import Any

import pytest

from openminion.modules.runtime.credentials import resolve_credential_ref
from openminion.tools.ops.contracts import OpsConfig, TransportFacts, TransportResult
from openminion.tools.ops.registry import TargetRegistry
from openminion.tools.ops.service import OpsService

pytestmark = pytest.mark.e2e


class RecordingTransport:
    def __init__(self, *, provider_request_id: str = "") -> None:
        self.calls: list[tuple[str, ...]] = []
        self.provider_request_id = provider_request_id

    def connect(self, target):
        return TransportFacts(
            kind=target.kind,
            platform=target.platform,
            connected=True,
        )

    inspect = connect

    def run(self, target, argv, **kwargs):
        del target, kwargs
        self.calls.append(argv)
        return TransportResult(
            argv=argv,
            return_code=0,
            stdout="ready",
            provider_request_id=self.provider_request_id,
        )

    def cancel(self, operation_id):
        del operation_id
        return False

    def close(self):
        return None


def _credential():
    return resolve_credential_ref(
        "remote-e2e",
        scope_kind="tool_family",
        scope_id="ops",
        env_name="OPENMINION_REMOTE_E2E",
    )


@pytest.mark.parametrize(
    ("payload", "provider_request_id"),
    [
        (
            {
                "target_id": "windows",
                "kind": "winrm",
                "platform": "windows",
                "environment": "staging",
                "address": "windows.example",
                "username": "operator",
                "credential_ref": _credential(),
                "ca_trust_path": "/etc/ssl/certs/ops.pem",
            },
            "",
        ),
        (
            {
                "target_id": "pod",
                "kind": "kubernetes",
                "environment": "staging",
                "credential_ref": _credential(),
                "context": "staging",
                "namespace": "agents",
                "pod": "worker-0",
            },
            "",
        ),
        (
            {
                "target_id": "node",
                "kind": "ssm",
                "environment": "staging",
                "credential_ref": _credential(),
                "account_id": "123456789012",
                "region": "us-west-2",
                "managed_node_id": "mi-123",
                "document_name": "AWS-RunShellScript",
            },
            "cmd-123",
        ),
        (
            {
                "target_id": "container",
                "kind": "container",
                "environment": "staging",
                "container": "worker",
                "docker_context": "staging",
            },
            "",
        ),
    ],
    ids=("winrm", "kubernetes", "ssm", "remote-container"),
)
def test_remote_kinds_share_plan_approval_job_and_evidence(
    payload: dict[str, Any], provider_request_id: str
) -> None:
    target = OpsConfig.model_validate({"targets": [payload]}).targets[0]
    transport = RecordingTransport(provider_request_id=provider_request_id)
    service = OpsService(
        targets=TargetRegistry((target,)),
        transports={target.kind: transport},
        transport_capabilities={target.kind: frozenset({"command"})},
    )

    plan = service.plan_command(
        target_id=target.target_id,
        argv=("printf", "ready"),
        session_id="remote-e2e",
    )
    job = service.run_plan(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        approval_id="approval-1",
    )
    evidence = service.inspect_evidence(job.evidence_id)

    assert job.status == "succeeded"
    assert transport.calls == [("printf", "ready")]
    assert evidence.target_revision == target.revision
    assert evidence.transport == target.kind
    assert evidence.approval_id == "approval-1"
    assert evidence.provider_request_id == provider_request_id
    assert evidence.claim_status == "observed"
