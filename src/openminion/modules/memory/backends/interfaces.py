"""Backend contract for durable memory owners below ``MemoryService``."""

from typing import Any, ClassVar, Protocol, TypeAlias

from openminion.modules.memory.contracts import (
    MEMORY_CONTRACT_VERSION,
    ensure_memory_contract_compatibility,
)
from openminion.modules.memory.models import (
    MemoryCandidate,
    MemoryRecord,
    MemoryRelation,
    MemoryTierTransition,
    MemoryType,
)
from openminion.modules.memory.portability.models import (
    MemoryBundleExportOptions,
    MemoryBundleImportOptions,
    MemoryBundleImportResult,
    MemoryBundleSnapshot,
)
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
    SearchQueryOptions,
)

KNOWLEDGE_BACKEND_VERSION = MEMORY_CONTRACT_VERSION

MemoryRecordLike: TypeAlias = MemoryRecord
MemoryRelationLike: TypeAlias = MemoryRelation
MemoryCandidateLike: TypeAlias = MemoryCandidate
MemoryTierTransitionLike: TypeAlias = MemoryTierTransition
MemoryTypeLike: TypeAlias = MemoryType
ListQueryOptionsLike: TypeAlias = ListQueryOptions
SearchQueryOptionsLike: TypeAlias = SearchQueryOptions
CandidateListOptionsLike: TypeAlias = CandidateListOptions
MemoryBundleExportOptionsLike: TypeAlias = MemoryBundleExportOptions
MemoryBundleImportOptionsLike: TypeAlias = MemoryBundleImportOptions
MemoryBundleSnapshotLike: TypeAlias = MemoryBundleSnapshot
MemoryBundleImportResultLike: TypeAlias = MemoryBundleImportResult


class KnowledgeBackend(Protocol):
    """Durable-memory backend contract below the orchestration service."""

    contract_version: ClassVar[str] = KNOWLEDGE_BACKEND_VERSION

    # Records
    def put_record(self, record: MemoryRecordLike) -> str: ...

    def upsert_record(
        self,
        scope: str,
        type: MemoryTypeLike,
        key: str,
        record_patch: dict[str, Any],
    ) -> MemoryRecordLike: ...

    def get_record(self, record_id: str) -> MemoryRecordLike | None: ...

    def list_records(self, options: ListQueryOptionsLike) -> list[MemoryRecordLike]: ...

    def search_records(
        self, options: SearchQueryOptionsLike
    ) -> list[MemoryRecordLike]: ...

    def invalidate_record(
        self,
        record_id: str,
        *,
        valid_to: str,
        reason: str,
    ) -> MemoryRecordLike: ...

    def supersede_record(
        self,
        old_record_id: str,
        new_record_id: str,
        reason: str = "",
    ) -> MemoryRecordLike: ...

    # Relations
    def put_relation(self, relation: MemoryRelationLike) -> str: ...

    def list_relations(
        self,
        record_id: str,
        *,
        relation_types: list[Any] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRelationLike]: ...

    def get_related_records(
        self,
        record_id: str,
        scopes: list[str],
        *,
        relation_types: list[Any] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRecordLike]: ...

    # Candidates
    def put_candidate(self, candidate: MemoryCandidateLike) -> str: ...

    def get_candidate(self, candidate_id: str) -> MemoryCandidateLike | None: ...

    def list_candidates(
        self, options: CandidateListOptionsLike
    ) -> list[MemoryCandidateLike]: ...

    def update_candidate(
        self,
        candidate_id: str,
        patch: dict[str, Any],
    ) -> MemoryCandidateLike: ...

    def promote_candidate(
        self,
        candidate_id: str,
        target_scope: str,
    ) -> MemoryRecordLike: ...

    # History / transitions
    def list_tier_transitions(
        self,
        *,
        record_id: str | None = None,
        scopes: list[str] | None = None,
        limit: int | None = None,
    ) -> list[MemoryTierTransitionLike]: ...

    def put_tier_transition(self, transition: MemoryTierTransitionLike) -> str: ...

    def history(
        self,
        scope: str,
        type: MemoryTypeLike,
        key: str,
    ) -> list[MemoryRecordLike]: ...

    # Portability hooks
    def export_snapshot(
        self,
        options: MemoryBundleExportOptionsLike,
    ) -> MemoryBundleSnapshotLike: ...

    def import_snapshot(
        self,
        snapshot: MemoryBundleSnapshotLike,
        options: MemoryBundleImportOptionsLike,
    ) -> MemoryBundleImportResultLike: ...


class KnowledgeBackendError(Exception):
    """Raised when a backend drifts from the required durable-memory shape."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def ensure_backend_compatibility(
    backend: Any,
    *,
    strict: bool = True,
) -> tuple[bool, list[str]]:
    """Validate a backend implementation against the KCE backend contract."""
    valid, raw_errors = ensure_memory_contract_compatibility(
        backend,
        role="backend",
        strict=False,
    )
    if valid:
        return True, []

    errors: list[str] = []
    for item in raw_errors:
        text = str(item or "")
        if text.startswith("missing member: "):
            errors.append(
                "Missing required backend method: "
                + text.split("missing member: ", 1)[1]
            )
            continue
        if text.startswith("non-callable member: "):
            errors.append(
                "Missing required backend method: "
                + text.split("non-callable member: ", 1)[1]
            )
            continue
        if text.startswith("version mismatch:"):
            declared = getattr(backend, "contract_version", "")
            errors.append(
                "Version mismatch: expected "
                f"{KNOWLEDGE_BACKEND_VERSION}, got {declared or '<missing>'}"
            )
            continue
        errors.append(text)
    errors.sort(key=lambda item: 0 if item.startswith("Version mismatch") else 1)

    if errors:
        if strict:
            raise KnowledgeBackendError(
                "KNOWLEDGE_BACKEND_INTERFACE_VIOLATION",
                f"Knowledge backend incompatible: {errors}",
            )
        return False, errors

    return True, []


__all__ = [
    "CandidateListOptionsLike",
    "KnowledgeBackend",
    "KnowledgeBackendError",
    "KNOWLEDGE_BACKEND_VERSION",
    "ListQueryOptionsLike",
    "MemoryBundleExportOptionsLike",
    "MemoryBundleImportOptionsLike",
    "MemoryBundleImportResultLike",
    "MemoryBundleSnapshotLike",
    "MemoryCandidateLike",
    "MemoryRecordLike",
    "MemoryRelationLike",
    "MemoryTierTransitionLike",
    "MemoryTypeLike",
    "SearchQueryOptionsLike",
    "ensure_backend_compatibility",
]
