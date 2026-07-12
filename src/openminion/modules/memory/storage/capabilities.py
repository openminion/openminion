from __future__ import annotations

from builtins import list as list_type
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from openminion.modules.memory.models import (
    MemoryCandidate,
    MemoryRelation,
    MemoryRelationType,
    MemoryRecord,
    MemoryTier,
    MemoryTierTransition,
    MemoryTierTransitionReason,
    MemoryType,
)
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
    SearchQueryOptions,
)


@dataclass(frozen=True)
class BackendCapabilities:
    supports_full_text: bool = True
    supports_vector_search: bool = False
    supports_candidate_workflow: bool = True
    supports_history: bool = True
    supports_capsule_cache: bool = False
    supports_transactions: bool = True


@runtime_checkable
class RecordStore(Protocol):
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

    def list(self, options: ListQueryOptions) -> list_type[MemoryRecord]: ...

    def list_scopes(self) -> list_type[str]: ...

    def touch_last_hit(self, record_id: str) -> None: ...

    def apply_outcome_feedback(
        self,
        record_ids: list_type[str],
        *,
        outcome: Literal["success", "failed", "timeout"],
        command_id: str,
        observed_at: str,
        feedback_delta: float,
    ) -> int: ...

    def candidate_put(self, candidate: MemoryCandidate) -> str: ...

    def put_relation(self, relation: MemoryRelation) -> str: ...

    def list_relations(
        self,
        record_id: str,
        *,
        relation_types: list_type[MemoryRelationType] | None = None,
        limit: int | None = None,
    ) -> list_type[MemoryRelation]: ...

    def get_related_records(
        self,
        record_id: str,
        scopes: list_type[str],
        *,
        relation_types: list_type[MemoryRelationType] | None = None,
        limit: int | None = None,
    ) -> list_type[MemoryRecord]: ...

    def candidate_get(self, candidate_id: str) -> MemoryCandidate | None: ...

    def candidate_list(
        self, options: CandidateListOptions
    ) -> list_type[MemoryCandidate]: ...

    def candidate_update(
        self, candidate_id: str, patch: dict[str, Any]
    ) -> MemoryCandidate: ...

    def promote_candidate(
        self, candidate_id: str, target_scope: str
    ) -> MemoryRecord: ...

    def supersede_by_contradiction(
        self, old_record_id: str, new_record_id: str, reason: str = ""
    ) -> MemoryRecord: ...

    def history(
        self, scope: str, type: MemoryType, key: str
    ) -> list_type[MemoryRecord]: ...


@runtime_checkable
class SearchIndex(Protocol):
    def search(self, options: SearchQueryOptions) -> list_type[MemoryRecord]: ...

    def retrieve_by_entities(
        self,
        entities: list_type[str],
        scopes: list_type[str],
        *,
        types: list_type[MemoryType] | None = None,
        tiers: list_type[MemoryTier] | None = None,
        limit: int | None = None,
    ) -> list_type[MemoryRecord]: ...

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
        scopes: list_type[str] | None = None,
        limit: int | None = None,
    ) -> list_type[MemoryTierTransition]: ...

    def put_tier_transition(self, transition: MemoryTierTransition) -> str: ...


@runtime_checkable
class VectorIndex(Protocol):
    def search(
        self,
        *,
        query: str,
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list_type[tuple[str, float, dict[str, Any] | None]]: ...


class CapabilityMemoryStore:
    """Adapter that composes record and search capabilities as a MemoryStore."""

    def __init__(
        self,
        *,
        records: RecordStore,
        search: SearchIndex,
        capabilities: BackendCapabilities | None = None,
    ) -> None:
        self._records = records
        self._search = search
        self.capabilities = capabilities or BackendCapabilities()

    def put(self, record: MemoryRecord) -> str:
        return self._records.put(record)

    def upsert(
        self, scope: str, type: MemoryType, key: str, record_patch: dict[str, Any]
    ) -> MemoryRecord:
        return self._records.upsert(scope, type, key, record_patch)

    def get(self, record_id: str) -> MemoryRecord | None:
        return self._records.get(record_id)

    def delete(self, record_id: str) -> None:
        self._records.delete(record_id)

    def invalidate(
        self,
        record_id: str,
        *,
        valid_to: str,
        reason: str,
    ) -> MemoryRecord:
        return self._records.invalidate(record_id, valid_to=valid_to, reason=reason)

    def tombstone(self, scope: str, type: MemoryType, key: str) -> None:
        self._records.tombstone(scope, type, key)

    def list(self, options: ListQueryOptions) -> list_type[MemoryRecord]:
        return self._records.list(options)

    def list_scopes(self) -> list_type[str]:
        return self._records.list_scopes()

    def list_records_by_goal_id(
        self,
        goal_id: str,
        *,
        scopes: list_type[str] | None = None,
        limit: int | None = None,
    ) -> list_type[MemoryRecord]:
        return self._records.list_records_by_goal_id(
            goal_id,
            scopes=scopes,
            limit=limit,
        )

    def touch_last_hit(self, record_id: str) -> None:
        self._records.touch_last_hit(record_id)

    def apply_outcome_feedback(
        self,
        record_ids: list_type[str],
        *,
        outcome: Literal["success", "failed", "timeout"],
        command_id: str,
        observed_at: str,
        feedback_delta: float,
    ) -> int:
        return self._records.apply_outcome_feedback(
            record_ids,
            outcome=outcome,
            command_id=command_id,
            observed_at=observed_at,
            feedback_delta=feedback_delta,
        )

    def search(self, options: SearchQueryOptions) -> list_type[MemoryRecord]:
        return self._search.search(options)

    def retrieve_by_entities(
        self,
        entities: list_type[str],
        scopes: list_type[str],
        *,
        types: list_type[MemoryType] | None = None,
        tiers: list_type[MemoryTier] | None = None,
        limit: int | None = None,
    ) -> list_type[MemoryRecord]:
        return self._search.retrieve_by_entities(
            entities=entities,
            scopes=scopes,
            types=types,
            tiers=tiers,
            limit=limit,
        )

    def transition_tier(
        self,
        record_id: str,
        *,
        to_tier: MemoryTier,
        transition_reason: MemoryTierTransitionReason,
        transition_at: str,
        meta: dict[str, Any] | None = None,
    ) -> MemoryTierTransition:
        return self._records.transition_tier(
            record_id,
            to_tier=to_tier,
            transition_reason=transition_reason,
            transition_at=transition_at,
            meta=meta,
        )

    def list_tier_transitions(
        self,
        *,
        record_id: str | None = None,
        scopes: list_type[str] | None = None,
        limit: int | None = None,
    ) -> list_type[MemoryTierTransition]:
        return self._records.list_tier_transitions(
            record_id=record_id,
            scopes=scopes,
            limit=limit,
        )

    def put_tier_transition(self, transition: MemoryTierTransition) -> str:
        return self._records.put_tier_transition(transition)

    def candidate_put(self, candidate: MemoryCandidate) -> str:
        return self._records.candidate_put(candidate)

    def put_relation(self, relation: MemoryRelation) -> str:
        return self._records.put_relation(relation)

    def list_relations(
        self,
        record_id: str,
        *,
        relation_types: list_type[MemoryRelationType] | None = None,
        limit: int | None = None,
    ) -> list_type[MemoryRelation]:
        return self._records.list_relations(
            record_id,
            relation_types=relation_types,
            limit=limit,
        )

    def get_related_records(
        self,
        record_id: str,
        scopes: list_type[str],
        *,
        relation_types: list_type[MemoryRelationType] | None = None,
        limit: int | None = None,
    ) -> list_type[MemoryRecord]:
        return self._records.get_related_records(
            record_id,
            scopes,
            relation_types=relation_types,
            limit=limit,
        )

    def candidate_get(self, candidate_id: str) -> MemoryCandidate | None:
        return self._records.candidate_get(candidate_id)

    def candidate_list(
        self, options: CandidateListOptions
    ) -> list_type[MemoryCandidate]:
        return self._records.candidate_list(options)

    def candidate_update(
        self, candidate_id: str, patch: dict[str, Any]
    ) -> MemoryCandidate:
        return self._records.candidate_update(candidate_id, patch)

    def promote_candidate(self, candidate_id: str, target_scope: str) -> MemoryRecord:
        return self._records.promote_candidate(candidate_id, target_scope)

    def supersede_by_contradiction(
        self, old_record_id: str, new_record_id: str, reason: str = ""
    ) -> MemoryRecord:
        return self._records.supersede_by_contradiction(
            old_record_id,
            new_record_id,
            reason=reason,
        )

    def history(
        self, scope: str, type: MemoryType, key: str
    ) -> list_type[MemoryRecord]:
        return self._records.history(scope, type, key)


__all__ = [
    "BackendCapabilities",
    "RecordStore",
    "SearchIndex",
    "VectorIndex",
    "CapabilityMemoryStore",
]
