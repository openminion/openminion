from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from openminion.modules.runtime.credentials import CredentialRef
from openminion.tools.ops import (
    BreakGlassGrant,
    EndpointTrust,
    OperationRequest,
    OperationTarget,
    TargetRegistry,
    TransportResult,
    build_argv,
    build_evidence,
    decide_operation_policy,
)
from openminion.tools.ops.api import target_view
from openminion.base.time import utc_now


def test_target_contract_requires_kind_specific_fields() -> None:
    with pytest.raises(ValidationError, match="container name"):
        OperationTarget(target_id="container", kind="container")
    with pytest.raises(ValidationError, match="address and credential_ref"):
        OperationTarget(target_id="remote", kind="ssh")

    remote = OperationTarget(
        target_id="remote",
        kind="ssh",
        address="host.example",
        credential_ref=CredentialRef(
            credential_id="ops-ssh",
            scope_kind="tool_family",
            scope_id="ops",
            source_kind="env",
            env_name="OPENMINION_OPS_SSH_PASSWORD",
            rotation_policy="static",
        ),
        endpoint_trust=EndpointTrust(host_key="ssh-ed25519 AAAAfixture"),
    )
    assert remote.credential_ref is not None
    assert "password" not in remote.model_dump()

    with pytest.raises(ValidationError, match="tool-family scope"):
        raw = remote.model_dump()
        raw["credential_ref"]["scope_id"] = "other_tools"
        OperationTarget.model_validate(raw)


def test_public_target_view_redacts_credentials_and_trust_material() -> None:
    target = OperationTarget(
        target_id="remote",
        kind="ssh",
        address="host.example",
        credential_ref=CredentialRef(
            credential_id="ops-ssh",
            scope_kind="tool_family",
            scope_id="ops",
            source_kind="env",
            env_name="OPENMINION_OPS_SSH_PASSWORD",
            rotation_policy="static",
        ),
        endpoint_trust=EndpointTrust(host_key="ssh-ed25519 AAAAfixture"),
    )

    view = target_view(target)

    assert view["credential_configured"] is True
    assert view["endpoint_trust_configured"] is True
    assert "credential_ref" not in view
    assert "endpoint_trust" not in view
    assert "OPENMINION_OPS_SSH_PASSWORD" not in str(view)


def test_target_registry_requires_monotonic_revisions() -> None:
    registry = TargetRegistry((OperationTarget(target_id="local", kind="local"),))
    with pytest.raises(ValueError, match="revision must increase"):
        registry.register(OperationTarget(target_id="local", kind="local"))
    registry.register(OperationTarget(target_id="local", kind="local", revision=2))
    assert registry.get("local").revision == 2


@pytest.mark.parametrize(
    "profile_id",
    [
        "host.snapshot",
        "network.inspect",
        "disk.usage",
        "memory.usage",
        "process.list",
    ],
)
def test_observation_profiles_build_argv_without_shell(profile_id: str) -> None:
    argv = build_argv(
        OperationRequest(
            operation_id="op-1",
            target_id="local",
            profile_id=profile_id,
        ),
        target_platform="linux",
    )
    assert argv
    assert not any(token in {"sh", "bash", "zsh", "-c", "-lc"} for token in argv)


def test_platform_profiles_require_structured_parameters() -> None:
    request = OperationRequest(
        operation_id="op-1",
        target_id="local",
        profile_id="service.inspect",
        parameters={"service": "example.service"},
    )
    assert build_argv(request, target_platform="linux")[0] == "systemctl"
    assert build_argv(request, target_platform="darwin")[0] == "launchctl"
    with pytest.raises(ValueError, match="parameter: service"):
        build_argv(
            request.model_copy(update={"parameters": {}}),
            target_platform="linux",
        )


def test_evidence_requires_observable_output_and_redacts_exact_values() -> None:
    request = OperationRequest(
        operation_id="op-1",
        target_id="local",
        profile_id="host.snapshot",
        session_id="session-1",
    )
    unknown = build_evidence(
        request,
        TransportResult(argv=("true",), return_code=0),
    )
    assert unknown.claim_status == "unknown"

    observed = build_evidence(
        request,
        TransportResult(argv=("printf",), return_code=0, stdout="token=secret"),
        redactions=("secret",),
    )
    assert observed.claim_status == "observed"
    assert observed.session_id == "session-1"
    assert observed.stdout_preview == "token=[REDACTED]"


def test_policy_defaults_read_only_and_gates_mutation() -> None:
    local = OperationTarget(target_id="local", kind="local")
    production = OperationTarget(
        target_id="production",
        kind="local",
        environment="production",
    )
    assert decide_operation_policy(local, risk="read").outcome == "allow"
    assert decide_operation_policy(local, risk="write_safe").outcome == "ask"
    assert decide_operation_policy(production, risk="write_safe").outcome == "deny"
    assert (
        decide_operation_policy(
            production,
            risk="write_safe",
            breakglass=BreakGlassGrant(
                target_id="production",
                reason="incident response",
                expires_at=(utc_now() + timedelta(minutes=5)).isoformat(),
            ),
        ).outcome
        == "ask"
    )
    expired = BreakGlassGrant(
        target_id="production",
        reason="expired incident",
        expires_at=(utc_now() - timedelta(minutes=5)).isoformat(),
    )
    wrong_target = expired.model_copy(
        update={
            "target_id": "other",
            "expires_at": (utc_now() + timedelta(minutes=5)).isoformat(),
        }
    )
    assert (
        decide_operation_policy(
            production,
            risk="write_safe",
            breakglass=expired,
        ).outcome
        == "deny"
    )
    assert (
        decide_operation_policy(
            production,
            risk="write_safe",
            breakglass=wrong_target,
        ).outcome
        == "deny"
    )
