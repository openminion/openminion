from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from sophiagraph.query import (
    CandidateListOptions,
    ListQueryOptions,
    RecordOrder,
    SearchQueryOptions,
)

from ..models import (
    MemoryCandidate,
    MemoryRelation,
    MemoryRelationType,
    MemoryRecord,
    MemoryTier,
    MemoryTierTransition,
    MemoryTierTransitionReason,
    MemoryType,
)


@runtime_checkable
class MemoryStore(Protocol):
    """Protocol describing the persistence boundary for openminion-memory."""

    def put(self, record: MemoryRecord) -> str: ...

    def upsert(
        self, scope: str, type: MemoryType, key: str, record_patch: dict[str, Any]
    ) -> MemoryRecord: ...

    def get(self, record_id: str) -> MemoryRecord | None: ...

    def delete(self, record_id: str) -> None: ...

    def invalidate(
        self,
        record_id: str,
        *,
        valid_to: str,
        reason: str,
    ) -> MemoryRecord: ...

    def tombstone(self, scope: str, type: MemoryType, key: str) -> None: ...

    def list(self, options: ListQueryOptions) -> list[MemoryRecord]: ...

    def search(self, options: SearchQueryOptions) -> list[MemoryRecord]: ...

    def list_scopes(self) -> list[str]: ...

    def touch_last_hit(self, record_id: str) -> None: ...

    def apply_outcome_feedback(
        self,
        record_ids: list[str],
        *,
        outcome: Literal["success", "failed", "timeout"],
        command_id: str,
        observed_at: str,
        feedback_delta: float,
    ) -> int: ...

    def retrieve_by_entities(
        self,
        entities: list[str],
        scopes: list[str],
        *,
        types: list[MemoryType] | None = None,
        tiers: list[MemoryTier] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRecord]: ...

    def list_records_by_goal_id(
        self,
        goal_id: str,
        *,
        scopes: list[str] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRecord]: ...

    def transition_tier(
        self,
        record_id: str,
        *,
        to_tier: MemoryTier,
        transition_reason: MemoryTierTransitionReason,
        transition_at: str,
        meta: dict[str, Any] | None = None,
    ) -> MemoryTierTransition: ...

    def list_tier_transitions(
        self,
        *,
        record_id: str | None = None,
        scopes: list[str] | None = None,
        limit: int | None = None,
    ) -> list[MemoryTierTransition]: ...

    def put_tier_transition(self, transition: MemoryTierTransition) -> str: ...

    def put_relation(self, relation: MemoryRelation) -> str: ...

    def list_relations(
        self,
        record_id: str,
        *,
        relation_types: list[MemoryRelationType] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRelation]: ...

    def get_related_records(
        self,
        record_id: str,
        scopes: list[str],
        *,
        relation_types: list[MemoryRelationType] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRecord]: ...

    def candidate_put(self, candidate: MemoryCandidate) -> str: ...

    def candidate_get(self, candidate_id: str) -> MemoryCandidate | None: ...

    def candidate_list(
        self, options: CandidateListOptions
    ) -> list[MemoryCandidate]: ...

    def candidate_update(
        self, candidate_id: str, patch: dict[str, Any]
    ) -> MemoryCandidate: ...

    def promote_candidate(
        self, candidate_id: str, target_scope: str
    ) -> MemoryRecord: ...

    def supersede_by_contradiction(
        self, old_record_id: str, new_record_id: str, reason: str = ""
    ) -> MemoryRecord: ...

    def history(self, scope: str, type: MemoryType, key: str) -> list[MemoryRecord]: ...


__all__ = (
    "CandidateListOptions",
    "ListQueryOptions",
    "MemoryStore",
    "RecordOrder",
    "SearchQueryOptions",
)
