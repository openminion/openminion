from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openminion.modules.runtime.credentials import CredentialRef

TargetKind = Literal["local", "container", "ssh"]
TargetPlatform = Literal["linux", "darwin"]
TargetEnvironment = Literal["fixture", "development", "staging", "production"]
ClaimStatus = Literal["observed", "failed", "partial", "unknown", "rolled_back"]
OperationRisk = Literal["read", "write_safe"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EndpointTrust(StrictModel):
    host_key: str = ""
    known_hosts_path: str = ""


class OperationTarget(StrictModel):
    target_id: str = Field(min_length=1)
    display_label: str = ""
    kind: TargetKind
    platform: TargetPlatform = "linux"
    environment: TargetEnvironment = "development"
    address: str = ""
    port: int = Field(default=22, ge=1, le=65535)
    username: str = ""
    container: str = ""
    credential_ref: CredentialRef | None = None
    endpoint_trust: EndpointTrust = EndpointTrust()
    policy_profile: str = "ops-linux-readonly"
    capabilities: tuple[str, ...] = ()
    workspace_scopes: tuple[str, ...] = ()
    log_scopes: tuple[str, ...] = ()
    service_scopes: tuple[str, ...] = ()
    max_concurrency: int = Field(default=4, ge=1, le=64)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    maintenance_window: str = ""
    enabled: bool = True
    labels: dict[str, str] = Field(default_factory=dict)
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_kind_fields(self) -> "OperationTarget":
        if self.kind == "local" and (self.address or self.container):
            raise ValueError("local targets cannot set address or container")
        if self.kind == "container" and not self.container:
            raise ValueError("container targets require a container name")
        if self.kind == "ssh":
            if not self.address or self.credential_ref is None:
                raise ValueError("ssh targets require address and credential_ref")
            if not (
                self.endpoint_trust.host_key or self.endpoint_trust.known_hosts_path
            ):
                raise ValueError("ssh targets require pinned endpoint trust")
            credential = self.credential_ref
            if (
                credential is None
                or credential.scope_kind != "tool_family"
                or credential.scope_id != "system_operations"
            ):
                raise ValueError(
                    "ssh credentials must use the system_operations tool-family scope"
                )
        return self


class TransportFacts(StrictModel):
    kind: TargetKind
    platform: TargetPlatform
    connected: bool
    capabilities: tuple[str, ...] = ()


class TransportReadResult(StrictModel):
    path: str
    content: str = ""
    truncated: bool = False


class OperationRequest(StrictModel):
    operation_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    parameters: dict[str, str | int | bool] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    idempotency_key: str = ""
    expected_target_revision: int | None = Field(default=None, ge=1)
    session_id: str = ""
    pack_id: str = "ops-linux-readonly"
    skill_id: str = ""
    tool_id: str = ""


class TransportResult(StrictModel):
    argv: tuple[str, ...]
    return_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False


class EvidenceRecord(StrictModel):
    evidence_id: str
    operation_id: str
    session_id: str = ""
    target_id: str
    target_revision: int = 0
    transport: str = ""
    profile_id: str
    pack_id: str = ""
    skill_id: str = ""
    tool_id: str = ""
    claim_status: ClaimStatus
    collected_at: str
    output_digest: str
    stdout_preview: str = ""
    stderr_preview: str = ""
    return_code: int
    reason: str = ""
    artifact_refs: tuple[str, ...] = ()
    policy_outcome: str = ""
    approval_id: str = ""
    command_hash: str = ""
    retention_until: str = ""
    redacted_parameters: dict[str, str | int | bool] = Field(default_factory=dict)
    before_facts: dict[str, str] = Field(default_factory=dict)
    after_facts: dict[str, str] = Field(default_factory=dict)
    failure: str = ""
    rollback_state: str = ""


class OperationJob(StrictModel):
    job_id: str
    request: OperationRequest
    target_revision: int
    status: JobStatus
    created_at: str
    updated_at: str
    evidence_id: str = ""
    error: str = ""
    expires_at: str = ""
    lease_owner: str = ""


class ChangePlan(StrictModel):
    plan_id: str
    target_id: str
    path: str = Field(min_length=1)
    content: str
    expected_digest: str = ""
    rollback_on_failure: bool = True
    expected_content: str | None = None
