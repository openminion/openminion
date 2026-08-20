from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openminion.modules.runtime.credentials import CredentialRef

TargetKind = Literal["local", "container", "ssh", "winrm", "kubernetes", "ssm"]
TargetPlatform = Literal["linux", "darwin", "windows"]
TargetEnvironment = Literal["fixture", "development", "staging", "production"]
TransportCapability = Literal["command", "file_read"]
SshAuthMode = Literal["password", "private_key"]
WinrmAuthMode = Literal["ntlm"]
ContainerRuntime = Literal["docker", "podman"]
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
    container_runtime: ContainerRuntime = "docker"
    docker_context: str = ""
    podman_connection: str = ""
    credential_ref: CredentialRef | None = None
    ssh_auth_mode: SshAuthMode = "password"
    endpoint_trust: EndpointTrust = EndpointTrust()
    policy_profile: str = "ops-readonly"
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
        has_remote_fields = bool(
            self.address
            or self.username
            or self.credential_ref
            or self.endpoint_trust.host_key
            or self.endpoint_trust.known_hosts_path
        )
        has_container_fields = bool(
            self.container
            or self.docker_context
            or self.podman_connection
            or self.container_runtime != "docker"
        )
        if self.kind == "local" and (has_container_fields or has_remote_fields):
            raise ValueError("local targets cannot set container or SSH fields")
        if self.kind == "container":
            if not self.container:
                raise ValueError("container targets require a container name")
            if has_remote_fields:
                raise ValueError("container targets cannot set SSH fields")
            if self.docker_context and self.podman_connection:
                raise ValueError(
                    "container targets cannot set both Docker context and Podman connection"
                )
            if self.docker_context and self.container_runtime != "docker":
                raise ValueError("Docker context requires the docker runtime")
            if self.podman_connection and self.container_runtime != "podman":
                raise ValueError("Podman connection requires the podman runtime")
        if self.kind == "ssh":
            if has_container_fields:
                raise ValueError("ssh targets cannot set container fields")
            if not self.address or not self.username or self.credential_ref is None:
                raise ValueError(
                    "ssh targets require address, username, and credential_ref"
                )
            if not (
                self.endpoint_trust.host_key or self.endpoint_trust.known_hosts_path
            ):
                raise ValueError("ssh targets require pinned endpoint trust")
            credential = self.credential_ref
            if (
                credential is None
                or credential.scope_kind != "tool_family"
                or credential.scope_id != "ops"
            ):
                raise ValueError("ssh credentials must use the ops tool-family scope")
            if (
                not credential.credential_id
                or credential.source_kind != "env"
                or not credential.env_name
            ):
                raise ValueError(
                    "ssh credentials must use a named environment reference"
                )
        elif self.ssh_auth_mode != "password":
            raise ValueError("non-SSH targets cannot set ssh_auth_mode")
        return self


class LocalTarget(OperationTarget):
    kind: Literal["local"] = "local"


class ContainerTarget(OperationTarget):
    kind: Literal["container"] = "container"


class SshTarget(OperationTarget):
    kind: Literal["ssh"] = "ssh"


def _validate_ops_credential(ref: CredentialRef | None, label: str) -> None:
    if ref is None or ref.scope_kind != "tool_family" or ref.scope_id != "ops":
        raise ValueError(f"{label} credentials must use the ops tool-family scope")
    if not ref.credential_id or ref.source_kind != "env" or not ref.env_name:
        raise ValueError(f"{label} credentials must use a named environment reference")


class WinrmTarget(OperationTarget):
    kind: Literal["winrm"] = "winrm"
    platform: Literal["windows"] = "windows"
    port: int = Field(default=5986, ge=1, le=65535)
    winrm_auth_mode: WinrmAuthMode = "ntlm"
    ca_trust_path: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_winrm(self) -> "WinrmTarget":
        if not self.address or not self.username:
            raise ValueError("winrm targets require address and username")
        _validate_ops_credential(self.credential_ref, "winrm")
        if self.port != 5986:
            raise ValueError("winrm targets require HTTPS port 5986")
        if (
            self.container
            or self.docker_context
            or self.podman_connection
            or self.endpoint_trust.host_key
            or self.endpoint_trust.known_hosts_path
        ):
            raise ValueError("winrm targets cannot set container or SSH trust fields")
        return self


class KubernetesTarget(OperationTarget):
    kind: Literal["kubernetes"] = "kubernetes"
    context: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    pod: str = Field(min_length=1)
    kubernetes_container: str = ""

    @model_validator(mode="after")
    def validate_kubernetes(self) -> "KubernetesTarget":
        _validate_ops_credential(self.credential_ref, "kubernetes")
        if (
            self.address
            or self.username
            or self.container
            or self.docker_context
            or self.podman_connection
            or self.endpoint_trust.host_key
            or self.endpoint_trust.known_hosts_path
        ):
            raise ValueError("kubernetes targets cannot set host or container fields")
        return self


class SsmTarget(OperationTarget):
    kind: Literal["ssm"] = "ssm"
    account_id: str = Field(min_length=1)
    region: str = Field(min_length=1)
    managed_node_id: str = Field(min_length=1)
    document_name: Literal["AWS-RunShellScript", "AWS-RunPowerShellScript"]

    @model_validator(mode="after")
    def validate_ssm(self) -> "SsmTarget":
        _validate_ops_credential(self.credential_ref, "ssm")
        if (
            self.address
            or self.username
            or self.container
            or self.docker_context
            or self.podman_connection
            or self.endpoint_trust.host_key
            or self.endpoint_trust.known_hosts_path
        ):
            raise ValueError("ssm targets cannot set host or container fields")
        expected = (
            "AWS-RunPowerShellScript"
            if self.platform == "windows"
            else "AWS-RunShellScript"
        )
        if self.document_name != expected:
            raise ValueError(f"ssm target platform requires document {expected}")
        return self


ConfiguredOperationTarget = Annotated[
    LocalTarget
    | ContainerTarget
    | SshTarget
    | WinrmTarget
    | KubernetesTarget
    | SsmTarget,
    Field(discriminator="kind"),
]


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
    provider_request_id: str = ""


class EvidenceRecord(StrictModel):
    evidence_id: str
    operation_id: str
    session_id: str = ""
    target_id: str
    target_revision: int = 0
    transport: str = ""
    profile_id: str
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
    provider_request_id: str = ""
    timed_out: bool = False
    truncated: bool = False


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


class CommandPlan(StrictModel):
    plan_id: str = Field(min_length=1)
    plan_hash: str = Field(min_length=64, max_length=64)
    target_id: str = Field(min_length=1)
    target_revision: int = Field(ge=1)
    argv: tuple[str, ...] = Field(min_length=1)
    cwd: str = ""
    timeout_seconds: float = Field(gt=0, le=300)
    session_id: str = ""
    idempotency_key: str = ""
    created_at: str = Field(min_length=1)
    expires_at: str = Field(min_length=1)
    policy_outcome: str = Field(min_length=1)


class OpsConfig(StrictModel):
    targets: tuple[ConfiguredOperationTarget, ...] = ()


class ChangePlan(StrictModel):
    plan_id: str
    target_id: str
    path: str = Field(min_length=1)
    content: str
    expected_digest: str = ""
    rollback_on_failure: bool = True
    expected_content: str | None = None
