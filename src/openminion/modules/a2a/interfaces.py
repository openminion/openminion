from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from openminion.modules.a2a.models import Envelope, JobRecord
    from openminion.modules.a2a.storage.base import StateStore, AuditStore
    from openminion.modules.a2a.artifacts import LocalArtifactStore
    from openminion.modules.a2a.policy import PolicyEngine


A2A_INTERFACE_VERSION = "v1"
_REQUIRED_RUNTIME_METHODS = (
    "register_agent",
    "list_agents",
    "call",
    "job_start",
    "job_status",
    "job_cancel",
    "recover_stale_jobs",
    "query_trace",
    "query_errors",
    "close",
)


class A2ARuntimeInterface(Protocol):
    contract_version: ClassVar[str] = A2A_INTERFACE_VERSION

    def __init__(
        self,
        *,
        state_store: StateStore,
        audit_store: AuditStore,
        artifact_store: LocalArtifactStore | None = None,
        policy_engine: PolicyEngine | None = None,
        max_inline_bytes: int = 16_384,
        recovery_stale_heartbeat_sec: int = 300,
        max_workers: int = 8,
    ) -> None: ...

    def register_agent(
        self,
        agent_id: str,
        capabilities: list[str],
        handler: Any,
        *,
        tags: list[str] | None = None,
    ) -> None: ...

    def list_agents(self) -> list[dict[str, Any]]: ...

    def call(self, envelope: Envelope) -> Envelope: ...

    def job_start(self, envelope: Envelope) -> str: ...

    def job_status(self, task_id: str) -> JobRecord: ...

    def job_cancel(self, task_id: str) -> JobRecord: ...

    def recover_stale_jobs(self) -> list[str]: ...

    def query_trace(self, trace_id: str, limit: int = 1000) -> list[dict[str, Any]]: ...

    def query_errors(
        self, *, since_seconds: int = 3600, limit: int = 1000
    ) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


def ensure_a2a_compatibility(
    runtime: Any, strict: bool = True
) -> tuple[bool, list[str]]:
    errors = []

    if not hasattr(runtime, "contract_version"):
        errors.append("Missing contract_version attribute")
    elif runtime.contract_version != A2A_INTERFACE_VERSION:
        errors.append(
            f"Version mismatch: expected {A2A_INTERFACE_VERSION}, "
            f"got {runtime.contract_version}"
        )

    for method in _REQUIRED_RUNTIME_METHODS:
        if not hasattr(runtime, method) or not callable(getattr(runtime, method)):
            errors.append(f"Missing required method: {method}")

    if errors:
        if strict:
            from openminion.modules.a2a.errors import A2AError

            raise A2AError(
                "A2A_RUNTIME_INTERFACE_VIOLATION", f"A2A runtime incompatible: {errors}"
            )
        return False, errors

    return True, []
