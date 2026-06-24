from typing import Any, Protocol, runtime_checkable

from .types import (
    MEMORY_CONTRACT_VERSION,
    MemoryCandidateDecision,
    MemoryCandidateRequest,
    MemoryCapsule,
    MemoryHit,
    MemoryProcedure,
    MemoryQuery,
    MemoryRuntimeSnapshot,
)


@runtime_checkable
class MemoryReadClient(Protocol):
    contract_version: str

    def search(self, query: MemoryQuery) -> list[MemoryHit]: ...

    def retrieve_by_entities(
        self,
        *,
        entities: list[str],
        scopes: list[str],
        types: list[str] | None = None,
        limit: int | None = None,
    ) -> list[MemoryHit]: ...


@runtime_checkable
class MemoryWriteClient(Protocol):
    contract_version: str

    def write_record(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: dict[str, Any] | str,
        tags: list[str] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> str: ...


@runtime_checkable
class MemoryCandidateClient(Protocol):
    contract_version: str

    def stage_candidate(self, request: MemoryCandidateRequest) -> str: ...

    def review_candidate(
        self,
        *,
        candidate_id: str,
        decision: str,
        reason_code: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> MemoryCandidateDecision: ...

    def promote_candidate(self, *, candidate_id: str, target_scope: str) -> str: ...


@runtime_checkable
class MemoryProcedureClient(Protocol):
    contract_version: str

    def get_procedure(self, *, procedure_id: str) -> MemoryProcedure | None: ...


@runtime_checkable
class MemoryIntrospectionClient(Protocol):
    contract_version: str

    def get_runtime_snapshot(
        self,
        *,
        session_id: str,
        agent_id: str,
        max_highlights: int = 5,
    ) -> MemoryRuntimeSnapshot | dict[str, Any]: ...


@runtime_checkable
class MemoryCapsuleClient(Protocol):
    contract_version: str

    def build_capsule(
        self,
        *,
        session_id: str,
        agent_id: str,
        strategy: str,
    ) -> MemoryCapsule: ...

    def refresh_capsule(
        self,
        *,
        session_id: str,
        agent_id: str,
        reason: str,
    ) -> MemoryCapsule: ...


__all__ = [
    "MEMORY_CONTRACT_VERSION",
    "MemoryReadClient",
    "MemoryWriteClient",
    "MemoryCandidateClient",
    "MemoryProcedureClient",
    "MemoryIntrospectionClient",
    "MemoryCapsuleClient",
]
